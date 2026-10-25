from contextlib import asynccontextmanager
import hashlib
import hmac
import os
from pathlib import Path
import random
import secrets
from threading import RLock
import time

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from database import Base, SessionLocal, engine, get_db
from models import ArticleProgress, Answer, Difficulty, Option, Question, QuestionType, Submission, Subject, User, UserSession

BASE_DIR = Path(__file__).resolve().parent
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PYTHON_DIFFICULTIES = (
    Difficulty.EASY,
    Difficulty.MEDIUM,
    Difficulty.HARD,
    Difficulty.ADVANCED,
    Difficulty.EXPERT,
)
FASTAPI_DIFFICULTIES = (*PYTHON_DIFFICULTIES, Difficulty.MASTER)
DIFFICULTY_LABELS = {
    Difficulty.EASY: "Легкий",
    Difficulty.MEDIUM: "Средний",
    Difficulty.HARD: "Сложный",
    Difficulty.ADVANCED: "Углубленный",
    Difficulty.EXPERT: "Эксперт",
    Difficulty.MASTER: "Мастер",
}
SUBJECT_LABELS = {
    Subject.PYTHON: "Python",
    Subject.PYTHON_BASES: "Основы Python",
    Subject.PYTHON_CONDITIONS: "Условный оператор",
    Subject.PYTHON_STRINGS: "Строки",
    Subject.PYTHON_DATA_STRUCTURES: "Массивы и структуры данных",
    Subject.PYTHON_LOOPS: "Циклы",
    Subject.PYTHON_OOP: "ООП",
    Subject.PYTHON_FUNCTIONS: "Функции",
    Subject.FASTAPI: "FastAPI",
    Subject.AIOGRAM: "Создание ботов на aiogram",
    Subject.THREADING: "Многопоточность",
    Subject.MULTIPROCESSING: "Многопроцессорность",
    Subject.ASYNCIO: "Асинхронность",
    Subject.GENERATOR: "Генератор",
    Subject.DECORATOR: "Декоратор",
    Subject.REGEX: "Регулярные выражения",
    Subject.PANDAS: "Pandas",
    Subject.UNITTEST: "unittest",
    Subject.PYTEST: "pytest",
    Subject.LINTERS: "Линтеры",
    Subject.FILES_FORMATS: "Работа с файлами и форматами",
    Subject.DATABASES: "Базы данных: PostgreSQL, SQLite и MySQL",
}
CONCURRENCY_SUBJECTS = (
    Subject.THREADING,
    Subject.MULTIPROCESSING,
    Subject.ASYNCIO,
)
PYTHON_BASES_SUBJECTS = (Subject.PYTHON_BASES,)
PYTHON_BASICS_TOPIC_SUBJECTS = (
    Subject.PYTHON_CONDITIONS,
    Subject.PYTHON_STRINGS,
    Subject.PYTHON_DATA_STRUCTURES,
    Subject.PYTHON_LOOPS,
    Subject.PYTHON_OOP,
    Subject.PYTHON_FUNCTIONS,
)
PYTHON_CONCEPT_SUBJECTS = (
    Subject.GENERATOR,
    Subject.DECORATOR,
    Subject.REGEX,
    Subject.PANDAS,
    Subject.UNITTEST,
    Subject.PYTEST,
    Subject.LINTERS,
    Subject.FILES_FORMATS,
    Subject.DATABASES,
)
ARTICLE_IDS = (
    "python",
    "py_conditions",
    "py_strings",
    "py_data",
    "py_loops",
    "py_oop",
    "py_functions",
    "fastapi",
    "aiogram",
    "threading",
    "multiprocessing",
    "asyncio",
    "generator",
    "decorator",
    "regex",
    "pandas",
    "unittest",
    "pytest",
    "linters",
    "files_formats",
    "databases",
)
GRADE_LABELS = {5: "Отлично", 4: "Хорошо", 3: "Удовлетворительно", 2: "Нужно повторить"}
DIFFICULTY_CACHE_TTL_SECONDS = 300
_difficulty_cache_lock = RLock()
_difficulty_cache: dict[
    tuple[str, ...],
    tuple[float, dict[str, list[dict[str, str | int]]]],
] = {}


class SubmittedAnswer(BaseModel):
    question_id: int
    selected_option_ids: list[int] = Field(default_factory=list)
    answer_text: str | None = None


class TestSubmission(BaseModel):
    answers: list[SubmittedAnswer]
    difficulty: Difficulty
    subject: Subject = Subject.PYTHON


class ArticleProgressUpdate(BaseModel):
    article_id: str = Field(min_length=1, max_length=80)


class AccountCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class LoginCredentials(BaseModel):
    login: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=6, max_length=128)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), 210_000
        ).hex()
        return hmac.compare_digest(expected, digest_hex)
    except (ValueError, TypeError):
        return False


def grade_for_score(score: int, max_score: int) -> int:
    percentage = score / max_score * 100 if max_score else 0
    if percentage >= 85:
        return 5
    if percentage >= 70:
        return 4
    if percentage >= 50:
        return 3
    return 2


def create_session(response: Response, user: User, db: Session) -> dict:
    token = secrets.token_urlsafe(48)
    db.add(UserSession(token=token, user=user))
    db.flush()
    account = {"id": user.id, "username": user.username, "email": user.email}
    db.commit()
    response.set_cookie(
        "session_token",
        token,
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
        max_age=60 * 60 * 24 * 30,
    )
    return account


def get_current_user(
    session_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not session_token:
        raise HTTPException(status_code=401, detail="Войдите в аккаунт")
    session = db.scalar(select(UserSession).where(UserSession.token == session_token))
    if not session:
        raise HTTPException(status_code=401, detail="Сессия истекла. Войдите снова")
    return session.user


def fastapi_question_candidates(difficulty: Difficulty) -> list[tuple[str, str, list[str]]]:
    questions = []
    resources = [
        "items", "users", "orders", "products", "tasks", "books", "teams", "events",
        "comments", "projects", "reports", "files", "messages", "payments", "profiles",
        "categories", "sessions", "notifications", "articles", "tokens",
    ]

    if difficulty == Difficulty.EASY:
        methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
        for resource in resources:
            for method in methods:
                decorator = method.lower()
                questions.append((
                    f"Как объявить маршрут {method} для ресурса /{resource}?",
                    f'@app.{decorator}("/{resource}")',
                    [
                        f'@app.{methods[(methods.index(method) + offset) % len(methods)].lower()}("/{resource}")'
                        for offset in (1, 2, 3)
                    ],
                ))
        return questions

    if difficulty == Difficulty.MEDIUM:
        for index, resource in enumerate(resources):
            limit = index + 2
            questions.append((
                f"Как объявить query-параметр limit для маршрута /{resource}, ограничив его диапазоном от 1 до {limit}?",
                f"limit: int = Query(1, ge=1, le={limit})",
                [f"limit: int = Query(1, gt=1, le={limit})", f"limit: str = Query(1, ge=1, le={limit})", f"limit: int = Path(1, ge=1, le={limit})"],
            ))
            questions.append((
                f"Как задать для POST /{resource} модель ответа {resource.title()}Out?",
                f'@app.post("/{resource}", response_model={resource.title()}Out)',
                [f'@app.post("/{resource}", request_model={resource.title()}Out)', f'@app.post("/{resource}", output={resource.title()}Out)', f'@app.post("/{resource}", schema={resource.title()}Out)'],
            ))
            questions.append((
                f"Как вернуть статус {201 + index} при создании ресурса /{resource}?",
                f'@app.post("/{resource}", status_code={201 + index})',
                [f'@app.post("/{resource}", response={201 + index})', f'@app.post("/{resource}", code={201 + index})', f'@app.post("/{resource}", status={201 + index})'],
            ))
            questions.append((
                f"Как подключить router_{resource} к приложению FastAPI?",
                f"app.include_router(router_{resource})",
                [f"app.add_router(router_{resource})", f"app.register(router_{resource})", f"include_router(app, router_{resource})"],
            ))
            questions.append((
                f"Как объявить необязательный query-параметр search для /{resource}?",
                "search: str | None = None",
                ["search: str = Required", "search: str | None = ...", "search: Query = str | None"],
            ))
        return questions

    if difficulty == Difficulty.HARD:
        for index, resource in enumerate(resources):
            dependency_name = f"get_{resource}_service"
            questions.append((
                f"Как объявить зависимость {dependency_name} для обработчика GET /{resource}?",
                f"service = Depends({dependency_name})",
                [f"service = Middleware({dependency_name})", f"service = Inject({dependency_name})", f"service = Depends[{dependency_name}]"],
            ))
            questions.append((
                f"Как зарегистрировать обработчик исключения {resource.title()}Error на приложении app?",
                f"@app.exception_handler({resource.title()}Error)",
                [f"@app.error_handler({resource.title()}Error)", f"@app.exception({resource.title()}Error)", f"@app.handle({resource.title()}Error)"],
            ))
            questions.append((
                f"Как объявить асинхронную зависимость с очисткой ресурса {resource} после ответа?",
                f"async def get_{resource}(): yield resource; await resource.close()",
                [f"async def get_{resource}(): return resource; await resource.close()", f"async def get_{resource}(): yield resource; resource.close()", f"def get_{resource}(): await resource.close(); return resource"],
            ))
            questions.append((
                f"Как добавить фоновую функцию send_{resource}_notice после отправки ответа?",
                f"background_tasks.add_task(send_{resource}_notice)",
                [f"await background_tasks.run(send_{resource}_notice)", f"response.add_task(send_{resource}_notice)", f"app.background(send_{resource}_notice)"],
            ))
            questions.append((
                f"Как ограничить целочисленный path-параметр {resource}_id значением не меньше {index + 1}?",
                f"{resource}_id: int = Path(ge={index + 1})",
                [f"{resource}_id: int = Query(ge={index + 1})", f"{resource}_id: int = Body(ge={index + 1})", f"{resource}_id: int = Path(gt={index + 1})"],
            ))
        return questions

    if difficulty == Difficulty.ADVANCED:
        for index, resource in enumerate(resources):
            questions.append((
                f"Как объявить контекст lifespan, который открывает и закрывает пул {resource}?",
                f"@asynccontextmanager async def lifespan(app): pool = await open_{resource}_pool(); yield; await pool.close()",
                [f"@app.on_event('startup') async def lifespan(app): yield; await close_{resource}_pool()", f"async def lifespan(app): return await open_{resource}_pool()", f"@asynccontextmanager def lifespan(app): await open_{resource}_pool(); return"],
            ))
            questions.append((
                f"Как объявить OAuth2 scope {resource}:read в зависимости FastAPI?",
                f"Security(get_current_user, scopes=['{resource}:read'])",
                [f"Depends(get_current_user, scope='{resource}:read')", f"OAuth2(get_current_user, permission='{resource}:read')", f"Security('{resource}:read', get_current_user)"],
            ))
            questions.append((
                f"Как применить строгую проверку значения поля {resource}_code длиной от 8 до 32 символов в модели Pydantic?",
                f"{resource}_code: str = Field(min_length=8, max_length=32, strict=True)",
                [f"{resource}_code: str = Query(min=8, max=32)", f"{resource}_code: int = Field(min_length=8, max_length=32)", f"{resource}_code: str = Field(length=8..32)"],
            ))
            questions.append((
                f"Как передать поток данных {resource} клиенту без предварительной сборки всего содержимого?",
                f"StreamingResponse(stream_{resource}(), media_type='application/octet-stream')",
                [f"JSONResponse(stream_{resource}())", f"FileResponse(stream_{resource}())", f"Response(await stream_{resource}())"],
            ))
            questions.append((
                f"Как отключить кэширование общей зависимости {resource} для конкретного параметра маршрута?",
                f"Depends(get_{resource}, use_cache=False)",
                [f"Depends(get_{resource}, cache=False)", f"Depends(get_{resource}, fresh=True)", f"NoCache(get_{resource})"],
            ))
        return questions

    if difficulty == Difficulty.MASTER:
        for index, resource in enumerate(resources):
            questions.append((
                f"Как назначить пользовательский класс маршрута {resource.title()}Route роутеру router_{resource}?",
                f"router_{resource}.route_class = {resource.title()}Route",
                [f"router_{resource}.route = {resource.title()}Route", f"router_{resource}.class = {resource.title()}Route", f"app.route_class({resource.title()}Route)"],
            ))
            questions.append((
                f"Как безопасно сохранить исходную генерацию OpenAPI и добавить в схему расширение x-{resource}-version?",
                f"original_openapi = app.openapi; app.openapi = custom_openapi",
                [f"app.openapi = custom_openapi; original_openapi = app.openapi", f"app.schema = custom_openapi", f"app.openapi_url = custom_openapi"],
            ))
            questions.append((
                f"Как исключить зависимость {resource}_client из кэша зависимостей только для одного параметра?",
                f"Depends(get_{resource}_client, use_cache=False)",
                [f"Depends(get_{resource}_client, cache=False)", f"Depends(get_{resource}_client, cached=False)", f"Depends(get_{resource}_client, scope='request')"],
            ))
            questions.append((
                f"Как проверить потоковую отправку ответа /{resource} асинхронным ASGI-клиентом?",
                f"async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client: response = await client.get('/{resource}')",
                [f"async with TestClient(app) as client: response = await client.get('/{resource}')", f"response = await app.get('/{resource}')", f"response = AsyncClient(app=app).get('/{resource}')"],
            ))
            questions.append((
                f"Как параметризовать зависимость {resource}_repository через callable-экземпляр с настройкой {index + 1}?",
                f"Depends({resource.title()}Repository(settings={index + 1}))",
                [f"Depends({resource.title()}Repository, settings={index + 1})", f"Inject({resource.title()}Repository({index + 1}))", f"Depends(settings={index + 1}, callable={resource.title()}Repository)"],
            ))
        return questions

    for index, resource in enumerate(resources):
        questions.append((
            f"Как создать пользовательский APIRoute для маршрутов /{resource}, переопределив get_route_handler?",
            f"class {resource.title()}Route(APIRoute): def get_route_handler(self): return custom_handler",
            [f"class {resource.title()}Route(HTTPRoute): def route_handler(self): return custom_handler", f"class {resource.title()}Route(FastAPI): def get_route_handler(self): return custom_handler", f"@app.route_class({resource.title()}Route) def handler(): pass"],
        ))
        questions.append((
            f"Как исключить вложенный параметр {resource}_db из повторного использования кэша зависимостей?",
            f"Depends(get_{resource}_db, use_cache=False)",
            [f"Depends(get_{resource}_db, cache=False)", f"Depends(get_{resource}_db, reuse=False)", f"Depends(get_{resource}_db, scope='new')"],
        ))
        questions.append((
            f"Как добавить отдельную схему OpenAPI для маршрута /{resource} без изменения стандартной генерации остальных маршрутов?",
            f"openapi_extra={{'x-{resource}-schema': {{'enabled': True}}}}",
            [f"response_model={{'x-{resource}-schema': True}}", f"app.openapi={{'x-{resource}-schema': True}}", f"schema_extra={{'route': '/{resource}'}}"],
        ))
        questions.append((
            f"Как в асинхронном тесте клиента ASGI запросить /{resource} через транспорт приложения?",
            f"async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client: await client.get('/{resource}')",
            [f"async with TestClient(app) as client: await client.get('/{resource}')", f"await app.get('/{resource}')", f"AsyncClient(app=app).get('/{resource}')"],
        ))
        questions.append((
            f"Как обеспечить выполнение teardown-зависимостей приложения {resource} в тесте с переопределенным lifespan?",
            f"with TestClient(app) as client: client.get('/{resource}')",
            [f"client = TestClient(app); client.get('/{resource}')", f"with TestClient(app, lifespan=False) as client: client.get('/{resource}')", f"await app.router.shutdown()"],
        ))
    return questions


def aiogram_question_candidates(difficulty: Difficulty) -> list[tuple[str, str, list[str]]]:
    questions = {
        Difficulty.EASY: [
            ("Как импортировать класс Bot в aiogram 3?", "from aiogram import Bot", ["from aiogram import Dispatcher", "from aiogram.types import Bot", "import Bot from aiogram"]),
            ("Как создать диспетчер aiogram 3?", "dp = Dispatcher()", ["dp = Bot()", "dp = Router()", "dp = Dispatcher(bot)"]),
            ("Как создать роутер для обработчиков?", "router = Router()", ["router = Dispatcher()", "router = Bot()", "router = Handler()"]),
            ("Как подключить роутер к диспетчеру?", "dp.include_router(router)", ["router.include_dispatcher(dp)", "dp.add_handler(router)", "dp.register(router)"]),
            ("Как запустить polling для бота?", "await dp.start_polling(bot)", ["await bot.start_polling(dp)", "dp.run(bot)", "await dp.polling(bot)"]),
            ("Какой тип обычно принимает обработчик входящего сообщения?", "message: Message", ["message: Bot", "message: Dispatcher", "message: Router"]),
            ("Как ответить на сообщение пользователя?", "await message.answer('Привет!')", ["message.answer('Привет!')", "await bot.answer(message)", "await message.reply_text('Привет!')"]),
            ("Как зарегистрировать обработчик команды /start?", "@router.message(CommandStart())", ["@router.message(Text('/start'))", "@router.command('/start')", "@dp.start('/start')"]),
            ("Из какого модуля импортировать фильтр CommandStart?", "from aiogram.filters import CommandStart", ["from aiogram.types import CommandStart", "from aiogram import CommandStart", "from aiogram.filters.command import Start"]),
            ("Как создать объект Bot с токеном?", "bot = Bot(token=TOKEN)", ["bot = Bot(TOKEN, dp)", "bot = Dispatcher(token=TOKEN)", "bot = Bot.create(TOKEN)"]),
            ("Какой метод отправляет сообщение в чат по его id?", "await bot.send_message(chat_id, 'Текст')", ["await bot.answer(chat_id, 'Текст')", "await dp.send(chat_id, 'Текст')", "bot.message(chat_id, 'Текст')"]),
            ("Как получить текст из объекта Message?", "message.text", ["message.message_text", "message.body", "message.content.text"]),
            ("Как зарегистрировать обработчик на сообщения через роутер?", "@router.message()", ["@router.update()", "@router.handle_message()", "@dp.message_handler()"]),
            ("Как импортировать тип Message в aiogram 3?", "from aiogram.types import Message", ["from aiogram import Message", "from aiogram.filters import Message", "from aiogram.types.message import BotMessage"]),
            ("Как зарегистрировать обработчик callback-запроса?", "@router.callback_query()", ["@router.callback()", "@router.inline_query()", "@router.message(callback=True)"]),
            ("Как подтвердить обработку callback-запроса?", "await callback.answer()", ["await callback.message.answer()", "callback.confirm()", "await bot.answer_callback(callback)"]),
            ("Как создать кнопку обычной клавиатуры?", "KeyboardButton(text='Помощь')", ["InlineKeyboardButton(text='Помощь')", "Button(label='Помощь')", "ReplyButton('Помощь')"]),
            ("Какой класс задает разметку обычной клавиатуры?", "ReplyKeyboardMarkup", ["InlineKeyboardMarkup", "KeyboardLayout", "ReplyMarkupBuilder"]),
            ("Какой класс нужен для кнопки под сообщением с callback_data?", "InlineKeyboardButton", ["KeyboardButton", "CallbackButton", "InlineButton"]),
            ("Как получить имя пользователя из входящего сообщения?", "message.from_user.full_name", ["message.user.name", "message.chat.username", "message.sender.fullname"]),
        ],
        Difficulty.MEDIUM: [
            ("Как отфильтровать сообщения, содержащие текст?", "@router.message(F.text)", ["@router.message(F.photo)", "@router.message(text=True)", "@router.text()"]),
            ("Как отфильтровать точный текст команды /help?", "@router.message(Command('help'))", ["@router.message(F.text == 'help')", "@router.command('help')", "@router.message(TextCommand('help'))"]),
            ("Как импортировать фильтр F для проверки полей события?", "from aiogram import F", ["from aiogram.filters import F", "from aiogram.types import F", "from aiogram.dispatcher import F"]),
            ("Как объявить группу состояний FSM?", "class Form(StatesGroup): name = State()", ["class Form(State): name = StatesGroup()", "class Form(Dispatcher): name = State()", "class Form: name = FSMContext()"]),
            ("Какой тип передать обработчику для управления состоянием FSM?", "state: FSMContext", ["state: State", "state: Dispatcher", "state: Bot"]),
            ("Как установить состояние name у FSM-контекста?", "await state.set_state(Form.name)", ["state.set(Form.name)", "await state.change(Form.name)", "await Form.name.set_state(state)"]),
            ("Как очистить состояние и данные FSM?", "await state.clear()", ["state.reset()", "await state.delete()", "await state.finish()"]),
            ("Как сохранить значение name в данных FSM?", "await state.update_data(name=value)", ["state.data['name'] = value", "await state.set_data('name', value)", "await state.save(name=value)"]),
            ("Как получить словарь данных FSM?", "data = await state.get_data()", ["data = state.data", "data = await state.data()", "data = state.get()"]),
            ("Как включить роутер с обработчиками FSM в диспетчер?", "dp.include_router(form_router)", ["form_router.set_dispatcher(dp)", "dp.register(form_router)", "dp.mount(form_router)"]),
            ("Как задать HTML parse mode по умолчанию в aiogram 3?", "Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))", ["Bot(token=TOKEN, parse_mode=ParseMode.HTML)", "Dispatcher(parse_mode='HTML')", "Bot(token=TOKEN, html=True)"]),
            ("Как импортировать настройки по умолчанию для Bot?", "from aiogram.client.default import DefaultBotProperties", ["from aiogram import DefaultBotProperties", "from aiogram.types import DefaultBotProperties", "from aiogram.client import BotDefaults"]),
            ("Как изменить текст существующего сообщения из callback-обработчика?", "await callback.message.edit_text('Готово')", ["await callback.edit_text('Готово')", "await message.update_text('Готово')", "await bot.edit(callback, 'Готово')"]),
            ("Как отфильтровать callback_data со значением open?", "@router.callback_query(F.data == 'open')", ["@router.message(F.callback == 'open')", "@router.callback_query(data='open')", "@router.callback('open')"]),
            ("Как импортировать тип CallbackQuery?", "from aiogram.types import CallbackQuery", ["from aiogram import CallbackQuery", "from aiogram.filters import CallbackQuery", "from aiogram.types import Callback"]),
            ("Как отправить пользователю фотографию по файловому пути?", "await message.answer_photo(FSInputFile('photo.jpg'))", ["await message.answer(FSInputFile('photo.jpg'))", "await message.send_photo('photo.jpg')", "await bot.photo(message, 'photo.jpg')"]),
            ("Как убрать обычную клавиатуру из следующего сообщения?", "await message.answer('Готово', reply_markup=ReplyKeyboardRemove())", ["await message.answer('Готово', keyboard=None)", "await message.remove_keyboard()", "await message.answer('Готово', reply_markup=InlineKeyboardMarkup())"]),
            ("Как передать текст кнопки обычной клавиатуры в обработчик?", "@router.message(F.text == 'Помощь')", ["@router.callback_query(F.data == 'Помощь')", "@router.message(F.button == 'Помощь')", "@router.keyboard('Помощь')"]),
            ("Как получить список поддерживаемых типов обновлений из диспетчера?", "dp.resolve_used_update_types()", ["dp.get_update_types()", "bot.resolve_updates()", "dp.allowed_updates()"]),
            ("Как запустить polling без обработки накопившихся старых обновлений?", "await dp.start_polling(bot, drop_pending_updates=True)", ["await dp.start_polling(bot, skip_updates=True)", "await bot.start_polling(drop_pending=True)", "dp.start_polling(bot, clean=True)"]),
        ],
        Difficulty.HARD: [
            ("Как задать Redis-хранилище FSM по URL в aiogram 3?", "storage = RedisStorage.from_url('redis://localhost:6379/0')", ["storage = RedisStorage('redis://localhost:6379/0')", "storage = FSMContext.from_url('redis://localhost:6379/0')", "storage = Dispatcher(redis='redis://localhost:6379/0')"]),
            ("Как передать хранилище FSM в Dispatcher?", "dp = Dispatcher(storage=storage)", ["dp = Dispatcher(state_storage=storage)", "dp = Dispatcher(redis=storage)", "dp.storage = storage"]),
            ("Как создать собственный фильтр для обработчиков?", "class IsAdmin(BaseFilter): async def __call__(self, message: Message) -> bool: ...", ["class IsAdmin(Filter): async def check(self, message): ...", "class IsAdmin(BaseMiddleware): async def __call__(self, message): ...", "def IsAdmin(message): return Filter(message)"]),
            ("Какой декоратор регистрирует обработчик ошибок роутера?", "@router.errors()", ["@router.exception()", "@router.error_handler()", "@router.on_error()"]),
            ("Как получить данные FSM и обновить их в обработчике?", "data = await state.get_data(); await state.update_data(step=2)", ["data = state.get(); state.data['step'] = 2", "data = await state.data(); await state.set(step=2)", "data = await state.read(); state.update(step=2)"]),
            ("Как проверить, что пользователь находится в состоянии Form.name?", "@router.message(Form.name)", ["@router.message(state=Form.name)", "@router.message(FSMState(Form.name))", "@router.state(Form.name)"]),
            ("Как создать типизированную callback-фабрику?", "class ItemCallback(CallbackData, prefix='item'): item_id: int", ["class ItemCallback(CallbackQuery, prefix='item'): item_id: int", "class ItemCallback(BaseModel, callback_prefix='item'): item_id: int", "class ItemCallback(CallbackData): prefix = 'item'; item_id = int"]),
            ("Как привязать callback-фабрику к фильтру обработчика?", "@router.callback_query(ItemCallback.filter())", ["@router.callback_query(ItemCallback())", "@router.callback_query(F.data(ItemCallback))", "@router.callback(ItemCallback.filter)"]),
            ("Как сформировать callback_data для экземпляра CallbackData?", "callback_data = ItemCallback(item_id=7).pack()", ["callback_data = ItemCallback(item_id=7).encode()", "callback_data = ItemCallback.pack(item_id=7)", "callback_data = str(ItemCallback(item_id=7))"]),
            ("Как использовать данные, извлеченные CallbackData-фильтром?", "async def handler(callback: CallbackQuery, callback_data: ItemCallback): ...", ["async def handler(callback: CallbackQuery, data: F.data): ...", "async def handler(callback: ItemCallback, query: CallbackQuery): ...", "async def handler(callback, item_id: int): ..."]),
            ("Как подключить middleware к обработчикам сообщений роутера?", "router.message.middleware(MyMiddleware())", ["router.middleware.message(MyMiddleware())", "router.message.use(MyMiddleware())", "router.add_middleware(MyMiddleware())"]),
            ("Какой базовый класс используется для middleware aiogram?", "BaseMiddleware", ["BaseFilter", "Middleware", "DispatcherMiddleware"]),
            ("Какой фильтр ограничивает обработчик сообщениями от администратора?", "@router.message(IsAdmin())", ["@router.message(BaseMiddleware(IsAdmin))", "@router.message(F.admin)", "@router.admin(IsAdmin)"]),
            ("Как указать стратегии FSM по пользователю в чате?", "FSMStrategy.USER_IN_CHAT", ["FSMStrategy.CHAT_USER", "FSMStrategy.PER_USER_CHAT", "FSMStrategy.USER_CHAT"]),
            ("Как создать Redis-хранилище с отдельной стратегией изоляции событий?", "storage = RedisStorage(redis=redis); isolation = RedisEventIsolation(redis=redis)", ["storage = RedisStorage(redis=redis, isolation=True)", "storage = RedisEventIsolation(redis=redis); isolation = RedisStorage(redis=redis)", "storage = FSMContext(redis=redis, isolation=RedisStorage)"]),
            ("Как передать изоляцию событий FSM в Dispatcher?", "dp = Dispatcher(storage=storage, events_isolation=isolation)", ["dp = Dispatcher(storage=storage, event_lock=isolation)", "dp = Dispatcher(isolation=isolation)", "dp.events_isolation = isolation"]),
            ("Как безопасно удалить inline-клавиатуру у сообщения?", "await callback.message.edit_reply_markup(reply_markup=None)", ["await callback.message.delete_keyboard()", "await callback.answer(reply_markup=None)", "await bot.remove_markup(callback.message)"]),
            ("Как зарегистрировать обработчик сообщения только в состоянии Form.email?", "@router.message(Form.email)", ["@router.message(F.state == Form.email)", "@router.state(Form.email)", "@router.message(state_filter=Form.email)"]),
            ("Как отправить документ из локального файла?", "await message.answer_document(FSInputFile('report.pdf'))", ["await message.answer_file('report.pdf')", "await message.answer_document(open('report.pdf'))", "await bot.send_file(message.chat.id, 'report.pdf')"]),
            ("Как ограничить роутер обработчиками определенного типа обновлений?", "dp.include_router(router)", ["dp.register_updates(router)", "router.bind(dp)", "dp.add_handlers(router)"]),
        ],
        Difficulty.ADVANCED: [
            ("Как корректно закрыть Redis-соединения после работы приложения?", "await storage.close(); await bot.session.close()", ["storage.close(); bot.close()", "await dp.close()", "await bot.shutdown(); storage.delete()"]),
            ("Как использовать webhook-обработчик aiogram вместе с aiohttp?", "SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path='/webhook')", ["WebhookRequestHandler(dp, bot).add(app)", "dp.register_webhook(app, '/webhook')", "app.add_route('/webhook', dp.webhook)"]),
            ("Из какого модуля импортировать SimpleRequestHandler?", "from aiogram.webhook.aiohttp_server import SimpleRequestHandler", ["from aiogram.webhook import SimpleRequestHandler", "from aiogram.webhook.aiohttp import SimpleRequestHandler", "from aiogram.contrib.webhook import SimpleRequestHandler"]),
            ("Как подключить startup/shutdown workflow aiogram к aiohttp-приложению?", "setup_application(app, dp, bot=bot)", ["dp.setup_aiohttp(app, bot)", "app.include_router(dp)", "register_aiogram(app, dp, bot)"]),
            ("Как задать секретный токен webhook при регистрации URL?", "await bot.set_webhook(url=url, secret_token=secret)", ["await bot.set_webhook(url=url, token=secret)", "await dp.webhook(url, secret=secret)", "bot.webhook_secret = secret"]),
            ("Как передать только используемые роутерами типы обновлений в webhook?", "allowed_updates=dp.resolve_used_update_types()", ["allowed_updates=dp.get_used_updates()", "update_types=dp.router_updates()", "allowed_updates=router.events"]),
            ("Как объявить общий параметр для обработчиков через workflow data?", "await dp.start_polling(bot, config=config)", ["dp.start_polling(bot, workflow=config)", "await bot.start_polling(dp, data=config)", "dp.workflow_data = config"]),
            ("Как получить общий параметр workflow data в обработчике?", "async def handler(message: Message, config: Config): ...", ["async def handler(message: Message, workflow: Config): ...", "async def handler(message: Message, dp.config): ...", "async def handler(message: Message, data['config']): ..."]),
            ("Как задать имя состояния для хранения данных пользователя в Redis?", "RedisStorage.from_url(url, state_ttl=timedelta(hours=1))", ["RedisStorage.from_url(url, ttl=3600)", "RedisStorage(url, expires=3600)", "RedisStorage.from_url(url, state_expire=3600)"]),
            ("Как отдельно ограничить срок хранения данных FSM в Redis?", "RedisStorage.from_url(url, data_ttl=timedelta(days=1))", ["RedisStorage.from_url(url, ttl_data=86400)", "RedisStorage(url, expire_data=86400)", "RedisStorage.from_url(url, data_expire=86400)"]),
            ("Как преобразовать данные, добавленные middleware, в аргумент обработчика?", "data['service'] = service; await handler(event, data)", ["event.service = service; await handler(event)", "handler.inject(service); await handler(event)", "data.set('service', service); handler(event, data)"]),
            ("Как получить только команды из текста сообщения фильтром aiogram?", "@router.message(Command())", ["@router.message(F.command)", "@router.command()", "@router.message(IsCommand())"]),
            ("Как маршрутизировать callback по полю action типизированной фабрики?", "@router.callback_query(ItemCallback.filter(F.action == 'delete'))", ["@router.callback_query(ItemCallback.action == 'delete')", "@router.callback_query(F.data.action == 'delete')", "@router.callback_query(ItemCallback(action='delete'))"]),
            ("Как настроить Redis-изоляцию событий для FSM?", "RedisEventIsolation(redis=redis)", ["RedisStorage(redis=redis, lock=True)", "FSMEventLock(redis=redis)", "RedisStorage.isolation(redis)"]),
            ("Как использовать стратегию FSM отдельно для каждого топика чата?", "FSMStrategy.CHAT_TOPIC", ["FSMStrategy.TOPIC", "FSMStrategy.CHAT_THREAD", "FSMStrategy.PER_TOPIC"]),
            ("Как передать storage и изоляцию событий при создании Dispatcher?", "Dispatcher(storage=storage, events_isolation=isolation)", ["Dispatcher(storage=storage, isolation=isolation)", "Dispatcher(fsm=storage, locks=isolation)", "Dispatcher(storage=storage, event_lock=isolation)"]),
            ("Как отправить фото по URL вместо локального файла?", "await message.answer_photo('https://example.com/photo.jpg')", ["await message.answer(FSInputFile('https://example.com/photo.jpg'))", "await message.send_url('https://example.com/photo.jpg')", "await bot.upload_photo('https://example.com/photo.jpg')"]),
            ("Как задать типы обновлений при запуске polling?", "await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())", ["await dp.start_polling(bot, update_types=dp.resolve_used_update_types())", "dp.start_polling(bot, updates='used')", "await bot.polling(dp, allowed=dp.updates)"]),
            ("Как в обработчике изменить callback-кнопки, не меняя текст сообщения?", "await callback.message.edit_reply_markup(reply_markup=keyboard)", ["await callback.message.edit_keyboard(keyboard)", "await callback.edit_markup(keyboard)", "await callback.answer(reply_markup=keyboard)"]),
            ("Как передать секрет для проверки запросов в aiohttp webhook-handler?", "SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret)", ["SimpleRequestHandler(dispatcher=dp, bot=bot, webhook_secret=secret)", "SimpleRequestHandler(dispatcher=dp, bot=bot, token=secret)", "SimpleRequestHandler(dispatcher=dp, bot=bot, secret=secret)"]),
        ],
        Difficulty.EXPERT: [
            ("Как обеспечить атомарную изоляцию FSM-событий между несколькими экземплярами бота?", "Использовать RedisStorage вместе с RedisEventIsolation на общем Redis", ["Использовать MemoryStorage в каждом процессе", "Хранить FSM только в переменной Python", "Создать отдельный Dispatcher без хранилища для каждого запроса"]),
            ("Как безопасно проверить секрет webhook-запроса средствами aiohttp-интеграции?", "Задать secret_token в set_webhook и secret_token в SimpleRequestHandler", ["Передать токен бота в query-параметре URL", "Сравнивать тело запроса с токеном бота", "Отключить проверку TLS и проверять IP клиента"]),
            ("Как избежать повторной обработки webhook-обновления при сбое между обработкой и ответом?", "Сделать обработчик идемпотентным и хранить ключ обработанного update_id", ["Всегда повторно вызывать start_polling", "Отключить подтверждение HTTP-ответом", "Сохранять только текст сообщения в локальной переменной"]),
            ("Как остановить polling при штатном завершении приложения и освободить HTTP-сессию?", "Дождаться остановки polling и закрыть bot.session в shutdown workflow", ["Вызвать bot.close() без ожидания polling", "Удалить объект Dispatcher", "Остановить event loop до завершения обработчиков"]),
            ("Как ограничить доступ к административному обработчику надежным способом?", "Проверять числовой user_id через собственный фильтр или middleware", ["Проверять отображаемое имя пользователя", "Скрыть кнопку администратора в клавиатуре", "Проверять текст команды после ее обработки"]),
            ("Как хранить токен Telegram-бота в production-конфигурации?", "Загружать его из переменной окружения или secret-хранилища", ["Записать токен в исходный код", "Передавать токен пользователю в команде /start", "Хранить токен в открытом репозитории в README"]),
            ("Как корректно организовать webhook за reverse proxy?", "Проверять секрет webhook, ограничить публичный маршрут и настроить HTTPS на proxy", ["Открыть внутренний порт приложения всему интернету без TLS", "Передавать токен бота в URL", "Принимать запросы от любого источника без проверки"]),
            ("Как минимизировать объем данных Telegram, принимаемых webhook-эндпоинтом?", "Передать allowed_updates с результатом dp.resolve_used_update_types()", ["Установить allowed_updates в пустой список", "Принимать все типы, а затем хранить необработанные payload навсегда", "Отключить регистрацию роутеров"]),
            ("Как безопасно отправить ответ на callback, если Telegram мог уже закрыть spinner?", "Обработать TelegramBadRequest для уже отвеченного callback", ["Повторять callback.answer() в бесконечном цикле", "Вызывать message.answer() вместо callback.answer() всегда", "Игнорировать все исключения Telegram API глобально"]),
            ("Где лучше размещать длительную CPU-bound задачу из обработчика бота?", "В отдельном worker-процессе или очереди задач", ["Выполнять синхронный цикл в event loop", "Запускать бесконечный цикл внутри callback-обработчика", "Блокировать event loop через time.sleep()"]),
            ("Как предотвратить утечку Redis-соединений при создании приложения?", "Создавать Redis-клиент один раз и закрывать его в lifecycle shutdown", ["Создавать новый Redis-клиент на каждое обновление", "Не закрывать Redis-клиент после остановки", "Хранить соединение в глобальной переменной без владельца"]),
            ("Как избежать гонки при последовательных действиях пользователя в FSM в нескольких процессах?", "Использовать распределенную изоляцию событий Redis для одного ключа FSM", ["Использовать только локальный asyncio.Lock в каждом процессе", "Увеличить число процессов polling", "Хранить состояние только в объекте Message"]),
            ("Как разделить обработчики большого бота без глобального смешивания роутеров?", "Разбить функции по Router и подключить их в явном порядке к Dispatcher", ["Создать отдельный Bot для каждой функции", "Регистрировать обработчики через приватные атрибуты Dispatcher", "Смешать все фильтры в одном обработчике"]),
            ("Как корректно обрабатывать исключения Telegram API при отправке сообщения?", "Перехватывать ожидаемые исключения aiogram и задавать ограниченные повторы для временных ошибок", ["Перехватывать BaseException и игнорировать ошибку", "Повторять запрос без лимита при любой ошибке", "Отключить проверку результата API"]),
            ("Как сделать повторную доставку одного update безопасной для оплаты заказа?", "Проверять уникальный update или idempotency key в транзакции БД до списания", ["Списывать деньги при каждом вызове обработчика", "Проверять только текст сообщения пользователя", "Хранить признак оплаты в памяти одного процесса"]),
            ("Как безопасно строить callback_data для сущности с большим набором полей?", "Использовать CallbackData с короткими полями и проверять значения на стороне сервера", ["Поместить полный JSON заказа без ограничения длины", "Кодировать в callback_data токен доступа", "Доверять идентификатору из callback без проверки прав"]),
            ("Как контролировать параллелизм при обработке тяжелых обновлений?", "Использовать ограниченный semaphore или очередь задач с заданной емкостью", ["Создавать неограниченное число фоновых задач", "Добавить time.sleep() в асинхронный обработчик", "Отключить подтверждение callback-запросов"]),
            ("Как изолировать сбой отдельного обработчика от всего polling-процесса?", "Обрабатывать ожидаемые ошибки локально и использовать error handlers для непредвиденных", ["Завершать процесс при любом исключении", "Подавлять все ошибки на уровне BaseException", "Перезапускать весь бот из обработчика каждого исключения"]),
            ("Как поддерживать актуальность схемы хранилища данных бота?", "Применять миграции БД отдельно от запуска обработчиков и проверять их перед релизом", ["Создавать таблицы заново при каждом update", "Хранить структуру данных только в FSM", "Менять схему вручную без версионирования"]),
            ("Как организовать наблюдаемость production-бота без записи секретов?", "Логировать update_id, тип события и trace id, исключая токены и персональные данные", ["Логировать полный заголовок Authorization и токен бота", "Записывать все сообщения вместе с паролями", "Отключить логи и метрики полностью"]),
        ],
    }
    questions = questions.get(difficulty, [])

    def add_variants(
        values: tuple[str, ...],
        templates: tuple[tuple[str, str, list[str]], ...],
    ) -> None:
        for title_template, correct_template, wrong_templates in templates:
            for value in values:
                questions.append((
                    title_template.format(value=value, value_lower=value.lower()),
                    correct_template.format(value=value, value_lower=value.lower()),
                    [
                        wrong.format(value=value, value_lower=value.lower())
                        for wrong in wrong_templates
                    ],
                ))

    if difficulty == Difficulty.EASY:
        add_variants(
            ("start", "help", "about", "settings", "menu", "contact", "support", "cancel"),
            (
                (
                    "Как зарегистрировать команду /{value} через роутер aiogram 3?",
                    "@router.message(Command('{value}'))",
                    ["@router.message(F.command == '{value}')", "@router.command('{value}')", "@dp.command('{value}')"],
                ),
                (
                    "Какой фильтр использовать для обработки команды /{value}?",
                    "Command('{value}')",
                    ["F.command('{value}')", "TextCommand('{value}')", "CommandFilter('/{value}')"],
                ),
                (
                    "Как добавить кнопку «{value}» в обычную клавиатуру?",
                    "KeyboardButton(text='{value}')",
                    ["InlineKeyboardButton(text='{value}')", "ReplyButton('{value}')", "Keyboard(text='{value}')"],
                ),
                (
                    "Как создать inline-кнопку с подписью и callback_data «{value}»?",
                    "InlineKeyboardButton(text='{value}', callback_data='{value}')",
                    ["KeyboardButton(text='{value}', callback_data='{value}')", "InlineButton(label='{value}', data='{value}')", "Button(text='{value}', callback='{value}')"],
                ),
                (
                    "Как отфильтровать callback-запросы с данными «{value}»?",
                    "@router.callback_query(F.data == '{value}')",
                    ["@router.message(F.data == '{value}')", "@router.callback_query(data='{value}')", "@router.callback(F.text == '{value}')"],
                ),
                (
                    "Как ответить текстом «{value}» на входящее сообщение?",
                    "await message.answer('{value}')",
                    ["message.answer('{value}')", "await bot.answer(message, '{value}')", "await message.reply_text('{value}')"],
                ),
                (
                    "Как отправить пользователю всплывающий ответ callback «{value}»?",
                    "await callback.answer('{value}')",
                    ["await callback.message.answer_callback('{value}')", "callback.answer('{value}')", "await bot.answer_callback(callback, '{value}')"],
                ),
                (
                    "Как создать роутер для раздела «{value}»?",
                    "router_{value} = Router()",
                    ["router_{value} = Dispatcher()", "router_{value} = Bot()", "router_{value} = Handler()"],
                ),
                (
                    "Как подключить роутер раздела «{value}» к диспетчеру?",
                    "dp.include_router(router_{value})",
                    ["dp.add_handler(router_{value})", "router_{value}.include_dispatcher(dp)", "dp.register(router_{value})"],
                ),
                (
                    "Как получить Telegram user_id из сообщения для сценария «{value}»?",
                    "message.from_user.id",
                    ["message.chat.username", "message.user.id", "message.sender.user_id"],
                ),
            ),
        )
    elif difficulty == Difficulty.MEDIUM:
        add_variants(
            ("name", "age", "city", "email", "phone", "address", "language", "comment"),
            (
                (
                    "Как объявить состояние {value} в группе Form на aiogram 3?",
                    "class Form(StatesGroup): {value} = State()",
                    ["class Form(State): {value} = StatesGroup()", "class Form: {value} = FSMContext()", "class Form(Dispatcher): {value} = State()"],
                ),
                (
                    "Как перевести FSM-контекст в состояние Form.{value}?",
                    "await state.set_state(Form.{value})",
                    ["state.set(Form.{value})", "await Form.{value}.set_state(state)", "await state.change(Form.{value})"],
                ),
                (
                    "Как сохранить значение {value} в данные FSM?",
                    "await state.update_data({value}=value)",
                    ["state.data['{value}'] = value", "await state.set_data('{value}', value)", "await state.save('{value}', value)"],
                ),
                (
                    "Как получить сохранённое поле {value} из данных FSM?",
                    "data = await state.get_data(); value = data.get('{value}')",
                    ["data = state.data; value = data['{value}']", "value = await state.get('{value}')", "value = state.get_data('{value}')"],
                ),
                (
                    "Как направить текстовое сообщение только обработчику состояния Form.{value}?",
                    "@router.message(Form.{value})",
                    ["@router.message(state=Form.{value})", "@router.state(Form.{value})", "@router.message(F.state == Form.{value})"],
                ),
                (
                    "Как отфильтровать текст сообщения, начинающийся с «{value}»?",
                    "@router.message(F.text.startswith('{value}'))",
                    ["@router.message(F.text == '{value}')", "@router.text.startswith('{value}')", "@router.message(text__startswith='{value}')"],
                ),
                (
                    "Как добавить кнопку «{value}» через InlineKeyboardBuilder?",
                    "builder.button(text='{value}', callback_data='{value}')",
                    ["builder.add(text='{value}', callback='{value}')", "builder.inline(text='{value}', data='{value}')", "builder.button(label='{value}', data='{value}')"],
                ),
                (
                    "Как изменить текст сообщения на «{value}» в обработчике callback?",
                    "await callback.message.edit_text('{value}')",
                    ["await callback.edit_text('{value}')", "await message.update_text('{value}')", "await bot.edit(callback, '{value}')"],
                ),
                (
                    "Как создать KeyboardButton с текстом «{value}» для ReplyKeyboardBuilder?",
                    "builder.button(text='{value}')",
                    ["builder.button(callback_data='{value}')", "builder.add_button(label='{value}')", "builder.reply(text='{value}')"],
                ),
                (
                    "Как отправить локальный файл «{value}.jpg» как фотографию?",
                    "await message.answer_photo(FSInputFile('{value}.jpg'))",
                    ["await message.answer(FSInputFile('{value}.jpg'))", "await message.send_photo('{value}.jpg')", "await bot.photo(message, '{value}.jpg')"],
                ),
            ),
        )
    elif difficulty == Difficulty.HARD:
        add_variants(
            ("Order", "Product", "Ticket", "Article", "Invoice", "Task", "Event", "Profile"),
            (
                (
                    "Как объявить CallbackData-фабрику {value}Callback с числовым id?",
                    "class {value}Callback(CallbackData, prefix='{value_lower}'): item_id: int",
                    ["class {value}Callback(CallbackQuery, prefix='{value_lower}'): item_id: int", "class {value}Callback(BaseModel, prefix='{value_lower}'): item_id: int", "class {value}Callback(CallbackData): prefix = '{value_lower}'; item_id = int"],
                ),
                (
                    "Как зарегистрировать callback-обработчик, отфильтрованный фабрикой {value}Callback?",
                    "@router.callback_query({value}Callback.filter())",
                    ["@router.callback_query({value}Callback())", "@router.message({value}Callback.filter())", "@router.callback({value}Callback.filter)"],
                ),
                (
                    "Как упаковать callback_data для {value_lower}_id=42?",
                    "callback_data = {value}Callback(item_id=42).pack()",
                    ["callback_data = {value}Callback(item_id=42).encode()", "callback_data = {value}Callback.pack(item_id=42)", "callback_data = str({value}Callback(item_id=42))"],
                ),
                (
                    "Как получить разобранную {value}Callback в обработчике callback-запроса?",
                    "async def handler(callback: CallbackQuery, callback_data: {value}Callback): ...",
                    ["async def handler(callback: {value}Callback, query: CallbackQuery): ...", "async def handler(callback: CallbackQuery, data: F.data): ...", "async def handler(callback, item_id: int): ..."],
                ),
                (
                    "Как зарегистрировать middleware {value}Middleware на событиях сообщений роутера?",
                    "router.message.middleware({value}Middleware())",
                    ["router.middleware.message({value}Middleware())", "router.message.use({value}Middleware())", "router.add_middleware({value}Middleware())"],
                ),
                (
                    "Какой базовый класс должен наследовать middleware {value}Middleware?",
                    "class {value}Middleware(BaseMiddleware): ...",
                    ["class {value}Middleware(BaseFilter): ...", "class {value}Middleware(DispatcherMiddleware): ...", "class {value}Middleware(Filter): ..."],
                ),
                (
                    "Как создать RedisStorage для FSM бота {value_lower} из URL?",
                    "storage = RedisStorage.from_url('redis://localhost:6379/0')",
                    ["storage = RedisStorage('redis://localhost:6379/0')", "storage = FSMContext.from_url('redis://localhost:6379/0')", "storage = Dispatcher(redis='redis://localhost:6379/0')"],
                ),
                (
                    "Как настроить FSM-изоляцию событий через Redis для раздела {value_lower}?",
                    "isolation = RedisEventIsolation(redis=redis)",
                    ["isolation = RedisStorage(redis=redis, lock=True)", "isolation = FSMEventLock(redis=redis)", "isolation = RedisStorage.isolation(redis)"],
                ),
                (
                    "Как передать FSM storage и изоляцию событий в Dispatcher {value_lower}?",
                    "dp = Dispatcher(storage=storage, events_isolation=isolation)",
                    ["dp = Dispatcher(storage=storage, isolation=isolation)", "dp = Dispatcher(fsm=storage, locks=isolation)", "dp.events_isolation = isolation"],
                ),
                (
                    "Как ограничить обработчик {value_lower} собственным фильтром администратора?",
                    "@router.message(IsAdmin())",
                    ["@router.message(BaseMiddleware(IsAdmin))", "@router.message(F.admin)", "@router.admin(IsAdmin)"],
                ),
            ),
        )
    elif difficulty == Difficulty.ADVANCED:
        add_variants(
            ("orders", "products", "tickets", "articles", "invoices", "tasks", "events", "profiles"),
            (
                (
                    "Как зарегистрировать aiohttp webhook route для ресурса /{value}?",
                    "SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path='/{value}')",
                    ["WebhookRequestHandler(dp, bot).add(app, path='/{value}')", "dp.register_webhook(app, '/{value}')", "app.add_route('/{value}', dp.webhook)"],
                ),
                (
                    "Как указать секрет webhook для endpoint /{value} при настройке Telegram?",
                    "await bot.set_webhook(url=url, secret_token=secret)",
                    ["await bot.set_webhook(url=url, token=secret)", "await dp.webhook(url, secret=secret)", "bot.webhook_secret = secret"],
                ),
                (
                    "Как разрешить webhook только для типов обновлений, используемых роутером {value}?",
                    "allowed_updates=dp.resolve_used_update_types()",
                    ["allowed_updates=dp.get_used_updates()", "update_types=dp.router_updates()", "allowed_updates=router.events"],
                ),
                (
                    "Как подключить startup/shutdown workflow Dispatcher для webhook-раздела {value}?",
                    "setup_application(app, dp, bot=bot)",
                    ["dp.setup_aiohttp(app, bot)", "app.include_router(dp)", "register_aiogram(app, dp, bot)"],
                ),
                (
                    "Как задать срок хранения FSM state для бота раздела {value}?",
                    "RedisStorage.from_url(url, state_ttl=timedelta(hours=1))",
                    ["RedisStorage.from_url(url, ttl=3600)", "RedisStorage(url, expires=3600)", "RedisStorage.from_url(url, state_expire=3600)"],
                ),
                (
                    "Как отдельно задать TTL данных FSM для раздела {value}?",
                    "RedisStorage.from_url(url, data_ttl=timedelta(days=1))",
                    ["RedisStorage.from_url(url, ttl_data=86400)", "RedisStorage(url, expire_data=86400)", "RedisStorage.from_url(url, data_expire=86400)"],
                ),
                (
                    "Как передать общий объект config в обработчики бота {value} при старте polling?",
                    "await dp.start_polling(bot, config=config)",
                    ["await dp.start_polling(bot, workflow=config)", "await bot.start_polling(dp, data=config)", "dp.workflow_data = config"],
                ),
                (
                    "Как получить объект config из workflow data в обработчике {value}?",
                    "async def handler(message: Message, config: Config): ...",
                    ["async def handler(message: Message, workflow: Config): ...", "async def handler(message: Message, dp.config): ...", "async def handler(message: Message, data['config']): ..."],
                ),
                (
                    "Как применить middleware-инъекцию зависимости {value} к обработчику?",
                    "data['service'] = service; await handler(event, data)",
                    ["event.service = service; await handler(event)", "handler.inject(service); await handler(event)", "data.set('service', service); handler(event, data)"],
                ),
                (
                    "Как зарегистрировать callback handler для действия delete в разделе {value}?",
                    "@router.callback_query(ItemCallback.filter(F.action == 'delete'))",
                    ["@router.callback_query(ItemCallback.action == 'delete')", "@router.callback_query(F.data.action == 'delete')", "@router.callback_query(ItemCallback(action='delete'))"],
                ),
            ),
        )
    elif difficulty == Difficulty.EXPERT:
        add_variants(
            ("заказа", "платежа", "подписки", "заявки", "билета", "профиля", "доставки", "уведомления"),
            (
                (
                    "Как не допустить повторного выполнения операции {value} при повторной доставке update?",
                    "Хранить обработанный update_id и проверять его атомарно до выполнения операции",
                    ["Повторять операцию при каждом вызове обработчика", "Сохранять флаг только в памяти процесса", "Сравнивать только текст сообщения"],
                ),
                (
                    "Как защитить данные {value} в масштабируемом боте с несколькими процессами?",
                    "Хранить их в общей БД и выполнять критические изменения в транзакции",
                    ["Хранить данные в глобальном dict каждого процесса", "Использовать FSM MemoryStorage между процессами", "Передавать данные в callback_data без проверки"],
                ),
                (
                    "Где выполнять долгую задачу обработки {value}, чтобы не блокировать event loop?",
                    "Передать задачу ограниченному worker-процессу или очереди задач",
                    ["Выполнить синхронный цикл в async handler", "Вызвать time.sleep() в обработчике", "Запустить неограниченное число create_task()"],
                ),
                (
                    "Как безопасно логировать события при обработке {value}?",
                    "Записывать update_id и trace id, исключая токены и персональные данные",
                    ["Записывать токен бота и все заголовки Authorization", "Сохранять полный текст с паролями", "Отключить обработку ошибок и логи"],
                ),
                (
                    "Как гарантировать корректный доступ администратора к операции {value}?",
                    "Проверять числовой Telegram user_id в фильтре или middleware на сервере",
                    ["Проверять отображаемое имя пользователя", "Скрыть кнопку администратора", "Доверять значению user_id из callback_data"],
                ),
                (
                    "Как ограничить повторные запросы к Telegram при временном сбое {value}?",
                    "Применить ограниченные повторы с backoff только для подходящих временных ошибок",
                    ["Повторять запрос бесконечно без задержки", "Игнорировать любую ошибку BaseException", "Сразу завершать все процессы бота"],
                ),
                (
                    "Как обрабатывать параллельные обновления, меняющие одни данные {value}?",
                    "Использовать распределенную блокировку или транзакционную конкуренцию в общем хранилище",
                    ["Полагаться на локальный asyncio.Lock в каждом процессе", "Увеличить число polling-процессов", "Хранить изменяемое состояние в объекте Message"],
                ),
                (
                    "Как безопасно принимать webhook-обновления для сценария {value} за reverse proxy?",
                    "Использовать HTTPS, проверять secret_token и ограничить публичный endpoint",
                    ["Передавать токен бота в URL", "Отключить TLS и проверку секрета", "Открыть внутренний порт всем источникам"],
                ),
                (
                    "Как поддерживать изменения схемы хранения данных для {value}?",
                    "Применять версионируемые миграции БД отдельно от обработки обновлений",
                    ["Пересоздавать таблицы на каждом update", "Хранить схему только в FSM", "Изменять production БД без миграций"],
                ),
                (
                    "Как гарантировать восстановление работы сценария {value} после рестарта одного экземпляра?",
                    "Использовать общее постоянное хранилище и проверять корректное завершение ресурсов",
                    ["Оставлять состояние только в локальной памяти", "Создавать новый Bot на каждый update", "Игнорировать ошибки shutdown"],
                ),
            ),
        )

    return questions


def concurrency_question_candidates(subject: Subject, difficulty: Difficulty) -> list[tuple[str, str, list[str]]]:
    contexts = {
        Subject.THREADING: (
            "обработке файлов", "загрузке изображений", "расчете статистики",
            "отправке уведомлений", "обращении к API", "чтении логов",
            "подготовке отчетов", "обслуживании клиентов", "поиске документов",
            "сжатии изображений", "сборе метрик", "обновлении каталога",
            "проверке ссылок", "обработке очереди задач", "синхронизации данных",
            "получении веб-страниц", "импорте записей", "обработке событий",
            "обновлении кэша", "проверке состояния сервиса",
        ),
        Subject.MULTIPROCESSING: (
            "обработке изображений", "расчете статистики", "сжатии архивов",
            "анализе больших файлов", "рендеринге кадров", "обработке матриц",
            "поиске по корпусу", "кодировании видео", "построении отчетов",
            "вычислении хешей", "обработке независимых заданий", "анализе данных",
            "генерации миниатюр", "обработке документов", "пакетном импорте",
            "преобразовании аудио", "расчете моделей", "индексации файлов",
            "проверке наборов данных", "выполнении независимых расчетов",
        ),
        Subject.ASYNCIO: (
            "запросах к API", "обработке веб-соединений", "загрузке страниц",
            "чтении сетевого потока", "отправке уведомлений", "подключениях клиентов",
            "обмене сообщениями", "ожидании ответа сервера", "работе с сокетами",
            "загрузке нескольких ресурсов", "обслуживании веб-запросов",
            "чтении асинхронного потока", "таймаутах внешнего сервиса",
            "обработке сетевых событий", "параллельных запросах",
            "опросе нескольких сервисов", "долгих операциях ввода-вывода",
            "передаче данных клиентам", "отправке запросов с ограничением скорости",
            "ожидании нескольких задач",
        ),
    }
    question_templates = {
        (Subject.THREADING, Difficulty.EASY): (
            ("Как запустить функцию work в отдельном потоке при {context}?",
             "thread = threading.Thread(target=work); thread.start()",
             ["thread = threading.Process(target=work); thread.start()", "thread = threading.Thread(work()); thread.run()", "thread = threading.start(work)"]),
            ("Как дождаться завершения потока при {context}?",
             "thread.join()",
             ["thread.wait()", "thread.finish()", "thread.close()"]),
            ("Как создать блокировку для общего счетчика при {context}?",
             "lock = threading.Lock()",
             ["lock = threading.Event()", "lock = threading.Thread()", "lock = threading.Semaphore(0)"]),
            ("Какой модуль содержит класс Thread, используемый при {context}?",
             "threading",
             ["multiprocessing", "asyncio", "concurrent"]),
            ("Как создать очередь Queue для обмена данными между потоками при {context}?",
             "queue = queue.Queue()",
             ["queue = asyncio.Queue()", "queue = multiprocessing.Pipe()", "queue = threading.Queue()"]),
        ),
        (Subject.THREADING, Difficulty.MEDIUM): (
            ("Как безопасно увеличить общий счетчик в критической секции при {context}?",
             "with lock: counter += 1",
             ["counter += 1; lock.release()", "with threading.Event(): counter += 1", "counter += 1 без синхронизации"]),
            ("Как отправить функцию в пул потоков и получить Future при {context}?",
             "future = executor.submit(work, item)",
             ["future = executor.start(work, item)", "future = threading.ThreadPool(work, item)", "future = executor.map(work(item))"]),
            ("Как получить результат завершившейся задачи из Future при {context}?",
             "result = future.result()",
             ["result = future.value", "result = future.join()", "result = future.get_result()"]),
            ("Как ожидать появления значения в threading.Event при {context}?",
             "event.wait()",
             ["event.join()", "await event.wait()", "event.acquire()"]),
            ("Какой потокобезопасный контейнер подходит для очереди задач при {context}?",
             "queue.Queue",
             ["list без блокировки", "обычный dict без синхронизации", "threading.Event"]),
        ),
        (Subject.THREADING, Difficulty.HARD): (
            ("Как передать исключение, возникшее внутри задачи ThreadPoolExecutor при {context}?",
             "Вызвать future.result(): исключение задачи будет повторно выброшено вызывающему коду",
             ["Исключения потоков автоматически прерывают весь процесс", "Проверить future.exception только до завершения задачи", "Поймать исключение через threading.current_exception()"]),
            ("Что обеспечивает lock при одновременном доступе потоков к общим данным во время {context}?",
             "Взаимоисключающий доступ к защищенному участку кода",
             ["Копирование данных между процессами", "Запуск корутин в event loop", "Автоматическую отмену ожидающих потоков"]),
            ("Почему GIL не устраняет гонку при чтении и изменении общего состояния во время {context}?",
             "Составная операция может чередоваться с выполнением другого потока",
             ["GIL действует только внутри async-функций", "GIL блокирует только операции записи", "GIL автоматически создает транзакции для объектов"]),
            ("Как избежать взаимной блокировки двух потоков при {context}?",
             "Захватывать несколько блокировок во всех потоках в одном и том же порядке",
             ["Захватывать блокировки в случайном порядке", "Не освобождать блокировки до завершения процесса", "Заменить Lock на Event без изменения логики"]),
            ("Для чего задать max_workers в ThreadPoolExecutor при {context}?",
             "Ограничить число одновременно работающих потоков",
             ["Ограничить число элементов в Future", "Задать количество потоков, которые всегда будут заблокированы", "Включить параллельное выполнение Python bytecode на всех ядрах"]),
        ),
        (Subject.THREADING, Difficulty.ADVANCED): (
            ("Как корректно остановить worker-потоки, ожидающие новые задания при {context}?",
             "Передать сигнал остановки, прекратить прием задач и дождаться завершения потоков",
             ["Принудительно уничтожить потоки вызовом thread.kill()", "Завершить процесс, пока потоки удерживают ресурсы", "Оставить daemon-потоки и не проверять их результат"]),
            ("Как дождаться задач из пула в порядке их фактического завершения при {context}?",
             "Использовать concurrent.futures.as_completed(futures)",
             ["Использовать threading.completed(futures)", "Вызвать join() у списка Future", "Использовать queue.Queue.completed()"]),
            ("Когда Condition.notify_all() уместен при {context}?",
             "Когда изменено условие, которого могут ждать несколько потоков",
             ["Чтобы освободить Lock, которым Condition не владеет", "Чтобы остановить все потоки навсегда", "Чтобы выполнить ожидающие функции в главном потоке"]),
            ("Как ограничить количество одновременных обращений к ресурсу при {context}?",
             "Обернуть доступ в threading.BoundedSemaphore с нужным числом разрешений",
             ["Использовать Event без ожидания", "Создать по Lock на каждый вызов и не делить их", "Вызвать Thread.join() перед каждой операцией"]),
            ("Как сохранить отдельное значение для каждого потока при {context}?",
             "Использовать threading.local()",
             ["Хранить все значения в одном общем атрибуте", "Использовать один глобальный список без блокировки", "Создать общий Event для хранения значения"]),
        ),
        (Subject.THREADING, Difficulty.EXPERT): (
            ("Как обеспечить предсказуемый shutdown сервиса с пулом потоков во время {context}?",
             "Прекратить подачу задач, вызвать shutdown(wait=True) и обработать ошибки Future",
             ["Вызвать os._exit() при первой завершившейся задаче", "Забыть executor и полагаться на сборщик мусора", "Остановить event loop вместо пула потоков"]),
            ("Как не допустить неограниченного роста очереди при всплеске нагрузки во время {context}?",
             "Использовать ограниченную очередь и явно применять backpressure или отказ",
             ["Увеличивать очередь без ограничения", "Создавать поток на каждое задание", "Отбрасывать блокировки и выполнять задачу дважды"]),
            ("Как диагностировать редкую гонку общего состояния при {context}?",
             "Минимизировать разделяемое состояние и воспроизводить сценарий под нагрузочным тестом с трассировкой",
             ["Считать, что GIL исключает гонки", "Добавить случайные sleep() в production", "Игнорировать несогласованные результаты"]),
            ("Как безопасно комбинировать блокировку с несколькими операциями над общим инвариантом при {context}?",
             "Защищать всю последовательность изменения и проверки одним согласованным lock",
             ["Блокировать только последнюю запись", "Создать новый несвязанный Lock в каждом методе", "Полагаться на атомарность нескольких Python-инструкций"]),
            ("Почему потоки обычно не ускоряют CPU-bound Python-код при {context} на стандартном CPython?",
             "GIL ограничивает одновременное исполнение Python bytecode потоками",
             ["Потоки не могут исполнять Python-код", "ThreadPoolExecutor всегда использует один поток", "Операционная система запрещает потоки выполнять вычисления"]),
        ),
        (Subject.MULTIPROCESSING, Difficulty.EASY): (
            ("Как запустить функцию work в отдельном процессе при {context}?",
             "process = multiprocessing.Process(target=work); process.start()",
             ["process = threading.Thread(target=work); process.start()", "process = multiprocessing.Process(work()); process.run()", "process = multiprocessing.start(work)"]),
            ("Как дождаться завершения дочернего процесса при {context}?",
             "process.join()",
             ["process.wait_thread()", "process.finish()", "process.close()"]),
            ("Как получить код завершения процесса после join() при {context}?",
             "process.exitcode",
             ["process.result", "process.return_value", "process.status_code()"]),
            ("Как создать пул процессов с контекстным менеджером при {context}?",
             "with multiprocessing.Pool() as pool:",
             ["with multiprocessing.ThreadPool() as pool:", "with multiprocessing.ProcessPool() as pool:", "with multiprocessing.Queue() as pool:"]),
            ("Что обычно делает multiprocessing.cpu_count() при {context}?",
             "Возвращает число логических CPU, доступных системе",
             ["Возвращает число запущенных Python-процессов", "Создает пул заданного размера", "Возвращает объем памяти процесса"]),
        ),
        (Subject.MULTIPROCESSING, Difficulty.MEDIUM): (
            ("Как безопасно запускать дочерние процессы переносимо при {context}?",
             "Поместить точку входа под if __name__ == '__main__':",
             ["Поместить весь код в глобальную область без guard", "Вызывать multiprocessing.freeze_support() вместо точки входа", "Запускать процесс во время импорта модуля"]),
            ("Как применить функцию к элементам и собрать результаты через пул при {context}?",
             "results = pool.map(work, items)",
             ["results = pool.submit(work, items)", "results = pool.run(work, items)", "results = process.map(work, items)"]),
            ("Как передать безопасный для межпроцессного обмена элемент в очередь при {context}?",
             "queue.put(item)",
             ["queue.write(item)", "queue.send_to_all(item)", "queue.append_shared(item)"]),
            ("Зачем использовать multiprocessing.Queue при {context}?",
             "Для обмена сериализуемыми объектами между процессами",
             ["Для совместного доступа к обычному локальному списку", "Для запуска корутин в одном event loop", "Для автоматического разделения одного Python-объекта без сериализации"]),
            ("Как проверить, завершился ли дочерний процесс во время {context}?",
             "process.is_alive()",
             ["process.is_running_thread()", "process.done()", "process.joined()"]),
        ),
        (Subject.MULTIPROCESSING, Difficulty.HARD): (
            ("Почему аргументы ProcessPoolExecutor должны быть сериализуемы при {context}?",
             "Задача и ее данные передаются границу процессов",
             ["Все функции Python обязаны быть сериализуемы", "Пул процессов выполняет только сетевой ввод-вывод", "Сериализация нужна только для потоков одного процесса"]),
            ("Что происходит при вызове future.result() для задачи пула процессов при {context}?",
             "Вызывающий код получает результат или исключение завершившейся задачи",
             ["Future запускает задачу повторно в главном процессе", "Возвращается PID без ожидания", "Исключения процесса всегда игнорируются"]),
            ("Как передать независимую CPU-bound функцию в ProcessPoolExecutor при {context}?",
             "future = executor.submit(work, item)",
             ["future = executor.start_thread(work, item)", "future = multiprocessing.run(work(item))", "future = process.submit_to_thread(work, item)"]),
            ("Почему нельзя полагаться на изменение обычного глобального списка дочерним процессом при {context}?",
             "Процессы имеют отдельные адресные пространства",
             ["Глобальные переменные доступны только корутинам", "Изменение списка отменяется GIL", "Дочерний процесс автоматически делает список неизменяемым"]),
            ("Как обрабатывать ошибку одной задачи ProcessPoolExecutor при {context}?",
             "Получить результат Future и обработать возникшее исключение",
             ["Проверить исключение через threading.current_exception()", "Ожидать, что исключение всегда завершит родительский процесс", "Перехватить ошибку вызовом process.join(exception=True)"]),
        ),
        (Subject.MULTIPROCESSING, Difficulty.ADVANCED): (
            ("Как явно выбрать способ старта процессов и избежать скрытой зависимости от платформы при {context}?",
             "Получить multiprocessing context через get_context('spawn') и создавать процессы из него",
             ["Изменить start method после запуска дочернего процесса", "Использовать только fork на каждой платформе", "Вызвать set_start_method() при каждом создании задачи"]),
            ("Как не допустить взаимной блокировки при передаче большого объема данных через multiprocessing.Queue во время {context}?",
             "Считывать элементы очереди и корректно дожидаться завершения производителя",
             ["Ожидать join производителя, не освобождая очередь", "Никогда не читать очередь", "Закрыть очередь до отправки всех данных"]),
            ("Как ограничить ресурсы при пакетной обработке через пул процессов во время {context}?",
             "Задать число workers и размер пакета с учетом CPU и памяти",
             ["Создать процесс для каждого элемента без ограничений", "Установить workers равным числу элементов всегда", "Удалить все ограничения на размер входных данных"]),
            ("Как передать процессы остановку при штатном завершении длительной обработки {context}?",
             "Передать явный сигнал остановки и дождаться процесса с ограниченным timeout",
             ["Завершить процесс через os._exit() без очистки", "Звать join() бесконечно без сигнала остановки", "Надеяться, что процесс заметит закрытие родительского терминала"]),
            ("Когда подходит multiprocessing.shared_memory при {context}?",
             "Для обмена крупными буферами между процессами с явным управлением временем жизни",
             ["Для передачи произвольных Python-объектов без сериализации", "Для запуска корутин", "Для защиты локальной переменной одного потока"]),
        ),
        (Subject.MULTIPROCESSING, Difficulty.EXPERT): (
            ("Как спроектировать надежный пул процессов для долгих CPU-задач при {context}?",
             "Ограничить размер пула и очереди, контролировать ошибки, timeout и штатное завершение",
             ["Создавать неограниченный пул и повторять ошибки бесконечно", "Хранить результаты только в памяти worker-процесса", "Принудительно убивать процессы при каждом timeout без учета состояния"]),
            ("Как избежать запуска побочных эффектов повторно при spawn во время {context}?",
             "Защитить запуск приложения блоком if __name__ == '__main__' и не запускать задачи при импорте",
             ["Перенести старт процесса на уровень импорта", "Удалить главный модуль после запуска", "Вызывать Pool() в каждом глобальном объявлении"]),
            ("Как обеспечить идемпотентность при повторной отправке задания worker-процессу во время {context}?",
             "Использовать стабильный идентификатор задания и атомарно фиксировать его результат",
             ["Считать, что процесс выполнит каждую задачу ровно один раз", "Сверять только PID worker", "Повторять запись результата без уникального ключа"]),
            ("Как ограничить сериализационные расходы при массовой передаче данных между процессами во время {context}?",
             "Передавать компактные задания или разделяемые буферы и измерять стоимость сериализации",
             ["Передавать полную копию всех данных каждой задаче", "Сериализовать объекты несколько раз для надежности", "Использовать глобальный объект без межпроцессного протокола"]),
            ("Когда выбор процессов предпочтительнее потоков при {context} на стандартном CPython?",
             "Когда задача CPU-bound и вычисления могут выполняться независимо в разных процессах",
             ["Для любого короткого сетевого запроса", "Когда критично разделять состояние без IPC", "Когда требуется общий объект Python с атомарным доступом"]),
        ),
        (Subject.ASYNCIO, Difficulty.EASY): (
            ("Как объявить корутину для сетевой операции при {context}?",
             "async def fetch_data(): ...",
             ["def async fetch_data(): ...", "await def fetch_data(): ...", "async function fetch_data(): ..."]),
            ("Как приостановить корутину до завершения другой при {context}?",
             "result = await fetch_data()",
             ["result = fetch_data().wait()", "result = yield fetch_data()", "result = asyncio.join(fetch_data())"]),
            ("Как запустить главный event loop для main() при {context}?",
             "asyncio.run(main())",
             ["asyncio.start(main())", "await asyncio.run(main())", "main().run_loop()"]),
            ("Как приостановить корутину на одну секунду без блокировки event loop при {context}?",
             "await asyncio.sleep(1)",
             ["time.sleep(1)", "asyncio.block(1)", "await time.wait(1)"]),
            ("Как создать задачу из корутины внутри работающего event loop при {context}?",
             "task = asyncio.create_task(coro())",
             ["task = asyncio.Thread(coro())", "task = asyncio.run(coro())", "task = coro.start()"]),
        ),
        (Subject.ASYNCIO, Difficulty.MEDIUM): (
            ("Как дождаться завершения нескольких корутин одновременно при {context}?",
             "results = await asyncio.gather(coro_a(), coro_b())",
             ["results = asyncio.gather(coro_a(), coro_b())", "results = await asyncio.join(coro_a(), coro_b())", "results = await threading.gather(coro_a(), coro_b())"]),
            ("Как задать timeout ожидания корутины при {context}?",
             "await asyncio.wait_for(coro(), timeout=2)",
             ["await asyncio.timeout(coro(), 2)", "asyncio.wait_for(coro(), timeout=2)", "await coro(timeout=2)"]),
            ("Как ограничить число одновременно выполняемых корутин при {context}?",
             "async with semaphore: await work()",
             ["with asyncio.Semaphore: await work()", "await semaphore.acquire(work())", "async with threading.Lock(): await work()"]),
            ("Как объявить асинхронный контекстный менеджер ресурса при {context}?",
             "async with resource:",
             ["with await resource:", "await with resource:", "async for resource:"]),
            ("Как отменить созданную asyncio Task при {context}?",
             "task.cancel()",
             ["task.kill()", "await task.stop()", "asyncio.cancel(task)"]),
        ),
        (Subject.ASYNCIO, Difficulty.HARD): (
            ("Почему вызов time.sleep() внутри async-функции вреден при {context}?",
             "Он блокирует event loop и задерживает выполнение других корутин",
             ["Он автоматически переводит функцию в поток", "Он отменяет текущую Task без исключения", "Он освобождает event loop для других задач"]),
            ("Как обработать отмену корутины и при этом гарантировать освобождение ресурса при {context}?",
             "Освобождать ресурс в finally и корректно распространять CancelledError",
             ["Перехватывать BaseException и молча игнорировать отмену", "Убрать finally и ждать завершения event loop", "Перезапустить отмененную корутину автоматически"]),
            ("Как безопасно задать таймаут группе ожидаемых корутин при {context}?",
             "Использовать asyncio.timeout() или asyncio.wait_for() и обработать TimeoutError",
             ["Использовать time.sleep() как таймаут", "Передать timeout в asyncio.create_task()", "Остановить event loop через loop.close()"]),
            ("Что делает asyncio.Queue.join() при {context}?",
             "Ждет, пока для каждого элемента очереди не будет вызван task_done()",
             ["Завершает event loop после одного элемента", "Ожидает появления первого элемента", "Отменяет все задачи, читающие очередь"]),
            ("Как получить исключение корутины, запущенной как Task, при {context}?",
             "Ожидать task или вызвать task.result() после ее завершения",
             ["Прочитать task.exception до запуска", "Исключения Task автоматически печатаются и всегда пробрасываются в вызывающий код", "Вызвать task.join()"]),
        ),
        (Subject.ASYNCIO, Difficulty.ADVANCED): (
            ("Как ограничить входящую нагрузку в асинхронном сервисе при {context}?",
             "Использовать bounded queue или semaphore и применять backpressure",
             ["Создавать Task на каждый запрос без ограничений", "Увеличить число event loop в одном потоке", "Не ожидать результат обработчика"]),
            ("Как выполнить блокирующую функцию в отдельном потоке из корутины при {context}?",
             "result = await asyncio.to_thread(blocking_func)",
             ["result = await blocking_func()", "result = asyncio.create_task(blocking_func())", "result = await threading.Thread(blocking_func)"]),
            ("Как обрабатывать несколько корутин структурированно, отменяя оставшиеся при ошибке во время {context}?",
             "Использовать asyncio.TaskGroup и обрабатывать ExceptionGroup",
             ["Запустить задачи через create_task и забыть ссылки на них", "Использовать asyncio.gather(return_exceptions=True) и игнорировать результаты", "Вызвать loop.stop() из каждой корутины"]),
            ("Как реализовать producer-consumer без потери незавершенных задач при {context}?",
             "Использовать asyncio.Queue, вызывать task_done() после обработки и ждать join()",
             ["Вызвать queue.get() без task_done() для каждого элемента", "Хранить задания только в списке без синхронизации", "Вызывать queue.join() до помещения задач"]),
            ("Почему нельзя вызывать asyncio.run() из уже запущенной корутины при {context}?",
             "В том же потоке уже работает event loop; следует await-ить корутину",
             ["asyncio.run() доступен только в Windows", "Корутины нельзя ожидать из других корутин", "asyncio.run() всегда создает поток вместо event loop"]),
        ),
        (Subject.ASYNCIO, Difficulty.EXPERT): (
            ("Как обеспечить корректную отмену группы задач при shutdown сервиса во время {context}?",
             "Прекратить прием работ, отменить или завершить TaskGroup и дождаться cleanup задач",
             ["Закрыть loop немедленно, не ожидая задач", "Игнорировать CancelledError во всех корутинах", "Создавать фоновые Task без учета их жизненного цикла"]),
            ("Как не допустить исчерпания памяти из-за медленного потребителя при {context}?",
             "Использовать ограниченную asyncio.Queue и ожидание свободной емкости",
             ["Создать неограниченную очередь", "Копить все результаты в глобальном списке", "Порождать новую Task для каждой записи без ограничения"]),
            ("Как предотвратить блокировку event loop CPU-bound вычислением при {context}?",
             "Перенести вычисления в процессный пул и ожидать результат асинхронно",
             ["Добавить await перед синхронным вычислением", "Запустить вычисление внутри asyncio.create_task()", "Заменить asyncio.sleep() на time.sleep()"]),
            ("Как безопасно обслуживать долгоживущие фоновые Task при {context}?",
             "Сохранять ссылки на задачи, наблюдать исключения и отменять их при shutdown",
             ["Не сохранять ссылки и игнорировать исключения", "Перезапускать event loop для каждой задачи", "Перехватывать BaseException и подавлять ошибки"]),
            ("Что необходимо учитывать при совместном доступе корутин к состоянию во время {context}?",
             "Синхронизировать составные изменения через asyncio.Lock либо устранить разделяемое изменяемое состояние",
             ["GIL автоматически делает последовательности await атомарными", "asyncio.Lock блокирует все процессы операционной системы", "Корутины никогда не переключаются между await"]),
        ),
    }
    templates = question_templates.get((subject, difficulty), ())
    return [
        (
            title.format(context=context),
            correct,
            wrong,
        )
        for title, correct, wrong in templates
        for context in contexts.get(subject, ())
    ]


def python_basics_topic_candidates(
    subject: Subject,
    difficulty: Difficulty,
) -> list[tuple[str, str, list[str]]]:
    words = (
        "python", "string", "loop", "array", "object", "function", "condition",
        "dictionary", "tuple", "method", "sequence", "variable", "inheritance",
        "iteration", "attribute", "module", "integer", "boolean", "set", "list",
        "class", "instance", "scope", "argument", "parameter", "return", "slice",
        "format", "search", "replace", "mutable", "immutable", "record", "stack",
        "queue", "sorted", "filter", "mapping", "default", "property", "counter",
        "parent", "child", "factory", "closure", "keyword", "reduce", "nested",
        "generator", "decorator",
    )
    candidates: list[tuple[str, str, list[str]]] = []
    difficulty_offset = PYTHON_DIFFICULTIES.index(difficulty) * 5

    def add(title: str, correct: object, wrong: list[object]) -> None:
        correct_label = repr(correct)
        wrong_labels = list(dict.fromkeys(repr(value) for value in wrong if repr(value) != correct_label))
        while len(wrong_labels) < 3:
            wrong_labels.append(repr(f"wrong option {len(wrong_labels) + 1}"))
        candidates.append((title, correct_label, wrong_labels[:3]))

    for index in range(5):
        number = index + 1 + difficulty_offset
        word = words[difficulty_offset + index]
        if subject == Subject.PYTHON_CONDITIONS:
            questions = (
                (f"Ветвь if {number} > 5: выполнится или будет пропущена?", number > 5, [number <= 5, None, "ошибка"]),
                (f"Какое значение у проверки {number} % 2 == 0?", number % 2 == 0, [number % 2 != 0, number, None]),
                (f"Верно ли составное условие {number} > 0 and {number} < 8?", 0 < number < 8, [number == 0, number >= 8, None]),
                (f"Что даст условие {number} < 3 or {number} == 10?", number < 3 or number == 10, [number < 3 and number == 10, number > 3, None]),
                (f"Какой результат у выражения 'четное' if {number} % 2 == 0 else 'нечетное'?", "четное" if number % 2 == 0 else "нечетное", ["четное" if number % 2 else "нечетное", "True", None]),
                (f"Как вычислится not ({number} <= 4)?", not number <= 4, [number <= 4, number, None]),
                (f"Попадет ли значение {number} в ветку elif x < 8 после проверки x < 3?", 3 <= number < 8, [number < 3, number >= 8, None]),
                (f"Что вернет bool([]) or bool([{number}])?", True, [False, number, None]),
                (f"Истинно ли выражение {number} in [2, 4, 6, 8]?", number in [2, 4, 6, 8], [number not in [2, 4, 6, 8], number, None]),
                (f"Сработает ли условие if {number} != 0 and 10 // {number} > 0?", number != 0 and 10 // number > 0, [number == 0, number < 0, None]),
                (f"Чему равно условие not ({number} < 3 or {number} > 7)?", not (number < 3 or number > 7), [number < 3 or number > 7, number == 3, None]),
                (f"Какую ветвь выберет if {number} == 1: 'first' elif {number} == 5: 'last' else: 'other'?", "first" if number == 1 else "last" if number == 5 else "other", ["first" if number != 1 else "other", "last", None]),
                (f"Какое значение вернет выражение () or {number}?", number, [0, False, None]),
                (f"Верно ли условие all(x < {number} for x in range(3))?", all(x < number for x in range(3)), [any(x < number for x in range(3)), not all(x < number for x in range(3)), None]),
                (f"Какой результат даст {number} >= 3 and {number} <= 7?", 3 <= number <= 7, [number < 3, number > 7, None]),
                (f"Какое значение у bool('{word}') and {number} != 0?", bool(word) and number != 0, [False, not bool(word), None]),
                (f"Что вернет выражение {number} if {number} is not None else 0?", number, [0, None, number + 1]),
                (f"Попадает ли {number} в range(1, 6)?", number in range(1, 6), [number not in range(1, 6), number < 6, None]),
                (f"Как классифицирует значение {number} выражение 'small' if {number} < 3 else 'large'?", "small" if number < 3 else "large", ["small" if number >= 3 else "large", "True", None]),
                (f"Верно ли, что {number} % 2 == 0 and {number} % 3 == 0?", number % 2 == 0 and number % 3 == 0, [number % 2 == 0 or number % 3 == 0, number % 6 != 0, None]),
            )
        elif subject == Subject.PYTHON_STRINGS:
            text = f"{word}-{number}"
            questions = (
                (f"Что вернет {text!r}.capitalize()?", text.capitalize(), [text.upper(), text.lower(), text]),
                (f"Чему равно выражение {text!r}.split('-')[0]?", text.split("-")[0], [text.split("-")[1], text, ""]),
                (f"Какой результат у {text!r}.replace('-', '_')?", text.replace("-", "_"), [text, text.upper(), text.replace("-", "")]),
                (f"Что вернет len({word!r})?", len(word), [len(word) + 1, len(word) - 1, number]),
                (f"Какой символ находится по индексу -1 в строке {word!r}?", word[-1], [word[0], word[1], word[-2]]),
                (f"Какое значение даст {word!r}[1:4]?", word[1:4], [word[:3], word[4:], word[::-1]]),
                (f"Сколько раз буква 'o' встречается в {word!r}?", word.count("o"), [word.count("o") + 1, len(word), 0]),
                (f"Верно ли, что {word!r}.startswith('{word[0]}')?", word.startswith(word[0]), [False, len(word), None]),
                (f"Что получится при соединении частей {text!r}.split('-') через ':'?", ":".join(text.split("-")), [text, text.replace("-", ""), ":".join(reversed(text.split("-")))]),
                (f"Какой текст вернет строковый срез {word!r}[::-1]?", word[::-1], [word, word.upper(), word[1:]]),
                (f"Верно ли, что строка {text!r} заканчивается на {str(number)!r}?", text.endswith(str(number)), [False, text.startswith(str(number)), None]),
                (f"Что вернет {text!r}.partition('-')[2]?", text.partition("-")[2], [text.partition("-")[0], word, ""]),
                (f"Сколько дефисов содержится в {text!r}?", text.count("-"), [text.count("-") + 1, 0, len(text)]),
                (f"Какой результат даст {word!r}.find('{word[0]}')?", word.find(word[0]), [word.find(word[0]) + 1, -1, len(word)]),
                (f"Чему равно выражение {word!r}[::2]?", word[::2], [word[1::2], word[::-1], word[:2]]),
                (f"Какой текст получится из f'{word}-{number}'.upper()?", f"{word}-{number}".upper(), [f"{word}-{number}", word.upper(), str(number)]),
                (f"Что вернет '-'.join([{word!r}, {str(number)!r}])?", f"{word}-{number}", [f"{word}{number}", f"{number}-{word}", word]),
                (f"Верно ли, что строка {word!r}.isalpha()?", word.isalpha(), [False, word.isdigit(), None]),
                (f"Какое слово вернет {text!r}.split('-', maxsplit=1)[1]?", text.split("-", maxsplit=1)[1], [word, text, ""]),
                (f"Чему равно len({text!r}.strip())?", len(text.strip()), [len(text) + 1, len(word), number]),
            )
        elif subject == Subject.PYTHON_DATA_STRUCTURES:
            values = [number, number + 1, number + 2]
            questions = (
                (f"После values.append({number + 3}) список {values} станет каким?", values + [number + 3], [values, values + [number + 3, number + 3], [number + 3] + values]),
                (f"Что удалит values.pop(1) из списка {values}?", number + 1, [number, number + 2, values]),
                (f"Что вернет tuple(({number}, {number + 1}))[0]?", number, [number + 1, 0, None]),
                (f"Чему равно len(set([{number}, {number}, {number + 1}]))?", 2, [3, 1, number]),
                (f"Что вернет profile.get('age', 0) для profile = {{'name': '{word}'}}?", 0, [number, None, "age"]),
                (f"Какой список вернет sorted([{number + 2}, {number}, {number + 1}])?", sorted([number + 2, number, number + 1]), [[number + 2, number, number + 1], sorted([number + 2, number, number + 1], reverse=True), [number, number + 2, number + 1]]),
                (f"Какое значение будет у mapping['{word}'] после mapping['{word}'] = {number}?", number, [number + 1, None, word]),
                (f"Чему равен результат [{number}, {number + 1}, {number + 2}][1:]?", values[1:], [values[:1], values, values[::-1]]),
                (f"Сколько пар вернет list({{'a': {number}, 'b': {number + 1}}}.items())?", 2, [1, 0, number]),
                (f"Какое объединение множеств даст {{1, {number}}} | {{{number}, {number + 1}}}?", sorted({1, number} | {number, number + 1}), [[1, number + 2], [number], [number + 2, number + 3]]),
                (f"Какое число вернет [{number}, {number + 1}, {number + 2}].index({number + 1})?", 1, [0, 2, number + 1]),
                (f"Какое значение запишет data.setdefault('count', {number}) в пустой словарь?", {"count": number}["count"], [number + 1, None, "count"]),
                (f"Сколько элементов будет в [{number}, {number + 1}].extend([{number + 2}, {number + 3}]) после выполнения операции?", 4, [2, 3, 5]),
                (f"Какой список получится после list(reversed([{number}, {number + 1}, {number + 2}]))?", [number + 2, number + 1, number], [values, [number + 1, number + 2, number], [number + 2, number, number + 1]]),
                (f"Какие ключи вернет sorted({{'{word}': 1, 'z': 2}})?", sorted([word, "z"]), ["z", word, sorted(["z", word], reverse=True)]),
                (f"Какой кортеж получится при распаковке first, second = ({number}, '{word}')?", (number, word), [(word, number), (number, number), word]),
                (f"Сколько общих элементов у множеств {{{number}, {number + 1}}} и {{{number + 1}, {number + 2}}}?", 1, [0, 2, number + 1]),
                (f"Какое значение вернет sum([n for n in [{number}, {number + 1}, {number + 2}] if n > {number}])?", (number + 1) + (number + 2), [number + number + 1, number + number + 2, number]),
                (f"Что вернет tuple(({number}, '{word}'))[1]?", word, [number, (number, word), None]),
                (f"Какой словарь получится из {{key: key * 2 for key in range(1, {number + 1})}}?", {key: key * 2 for key in range(1, number + 1)}, [{key: key for key in range(1, number + 1)}, {1: 2}, list(range(2, 2 * number + 1, 2))]),
            )
        elif subject == Subject.PYTHON_LOOPS:
            questions = (
                (f"Какие числа напечатает for i in range({number}, {number + 3})?", list(range(number, number + 3)), [list(range(number, number + 2)), list(range(number + 1, number + 4)), list(range(number + 3, number, -1))]),
                (f"Сколько итераций выполнит while count < {number}, если count начинается с 0 и растет на 1?", number, [number - 1, number + 1, 0]),
                (f"Чему равна сумма чисел, оставшихся после continue для четных значений в range({number + 1})?", sum(value for value in range(number + 1) if value % 2), [sum(value for value in range(number + 1) if value % 2 == 0), number, 0]),
                (f"Какое значение останется в total после суммирования range(1, {number + 1})?", sum(range(1, number + 1)), [sum(range(number)), sum(range(1, number + 2)), number]),
                (f"Сколько раз выполнится внутреннее тело двух циклов: range({number}) и range(2)?", number * 2, [number, number + 2, 2]),
                (f"Что выведет enumerate(['{word}', 'end']) на второй итерации?", (1, "end"), [(0, word), (2, "end"), "end"]),
                (f"Какое значение будет у последнего элемента списка [x * 2 for x in range({number + 1})]?", number * 2, [number, (number + 1) * 2, 0]),
                (f"Сколько чисел пропускает цикл с break при i == {number}, если обход начинается с 0?", number + 1, [number, number + 2, 0]),
                (f"Какой символ обработается первым в цикле for ch in reversed('{word}')?", word[-1], [word[0], word[1], ""]),
                (f"Сколько значений словаря обработает for key in {{'{word}': 1, 'end': 2}}?", 2, [1, 0, number]),
                (f"Что выведет for i in range({number}, {number + 5}, 2) при последней итерации?", number + 4, [number + 5, number + 3, number + 2]),
                (f"Чему равен результат sum(value for value in range({number + 1}) if value % 2 == 0)?", sum(value for value in range(number + 1) if value % 2 == 0), [sum(value for value in range(number + 1) if value % 2), number, 0]),
                (f"Какое значение будет у count после while count > 0, если count начинается с {number} и уменьшается на 1?", 0, [number, 1, -1]),
                (f"Какой блок выполнится после for i in range({number}), если цикл завершится без break?", "else", ["break", "finally", "except"]),
                (f"Сколько пар сформирует zip([1, 2], ['{word}', 'end'])?", 2, [1, 3, 0]),
                (f"Какой индекс получит '{word}' при enumerate(['skip', '{word}'], start=5)?", 6, [5, 1, 7]),
                (f"Чему равен результат вложенного цикла: sum(i + j for i in range({number}) for j in range(2))?", number * (number - 1) + number, [number * 2, number * (number - 1), number + 2]),
                (f"Сколько раз выполнится тело цикла, если range({number}) прерывается при i == {number // 2}?", number // 2 + 1, [number, number // 2, number // 2 + 2]),
                (f"Какой элемент будет последним в списке [item for item in reversed(range({number + 1}))]?", 0, [number, 1, None]),
                (f"Сколько значений больше {number} обработает цикл по range({number + 3})?", 2, [1, 3, number]),
            )
        elif subject == Subject.PYTHON_OOP:
            questions = (
                (f"В экземпляре Item{number} поле self.name получает '{word}'. Как прочитать это поле у item?", "item.name", ["Item.name", "self.name", "item->name"]),
                (f"Что вернет isinstance(Child{number}(), Parent), если Child{number} наследует Parent?", True, [False, "Child", None]),
                (f"Какой метод вызывается при создании экземпляра Item{number} и обычно задает его поля?", "__init__", ["__new_instance__", "init", "constructor"]),
                (f"Если дочерний класс переопределил speak() и возвращает '{word}', какой результат даст Child{number}().speak()?", word, ["Parent", None, "speak"]),
                (f"Как обратиться к реализации метода родителя из переопределенного метода класса Child{number}?", "super().method()", ["parent.method()", "self.super()", "Child.method(super)"]),
                (f"Сколько у класса с одним полем класса value={number} независимых значений после создания двух пустых экземпляров?", 1, [2, 0, number]),
                (f"Какой декоратор используют для Factory{number}.create, если метод получает класс через cls?", "@classmethod", ["@staticmethod", "@property", "@classmethod()"]),
                (f"Какой декоратор дает доступ к вычисляемому атрибуту box{number}.area без вызова box{number}.area()?", "@property", ["@staticmethod", "@classmethod", "@attribute"]),
                (f"Метод __init__ класса Box{number} сохраняет width={number} и height=2. Чему равно произведение полей?", number * 2, [number + 2, number, number * 2 + 1]),
                (f"Какой метод класса Item{number} задает текст для str(item)?", "__str__", ["__text__", "__repr_str__", "to_string"]),
                (f"Какой декоратор обозначает метод Util{number}.double, которому не нужен self или cls?", "@staticmethod", ["@classmethod", "@property", "@method"]),
                (f"Какой параметр должен стоять первым у обычного метода экземпляра в классе Item{number}?", "self", ["cls", "this", "instance"]),
                (f"Если у Parent{number} есть поле name, а Child{number} наследуется от Parent{number}, доступно ли это поле экземпляру Child?", True, [False, "Parent", None]),
                (f"Что напечатает Child{number}().speak(), если метод вызывает super().speak(), а метод родителя возвращает 'base'?", "base", ["child", None, "super"]),
                (f"Сколько независимых полей count будет у двух экземпляров Item{number}, если каждый задает self.count в __init__?", 2, [1, 0, number]),
                (f"Какой атрибут читает box{number}.area, если area объявлен через @property?", "area", ["area()", "__area__", "get_area"]),
                (f"Что обычно означает композиция: объект Library{number} хранит список Book объектов?", "Library содержит Book", ["Book наследуется от Library", "Library и Book — один объект", "Book является методом Library"]),
                (f"Какой специальный метод обычно отвечает за диагностическое представление объекта repr(item) класса Item{number}?", "__repr__", ["__str__", "__format__", "__debug__"]),
                (f"Какой объект передается в параметр cls метода Factory{number}.create с декоратором @classmethod?", "класс", ["экземпляр", "модуль", "функция"]),
                (f"Что произойдет с полем экземпляра, если присвоить item{number}.status = 'ready' при одноименном поле класса?", "создастся или изменится поле экземпляра", ["изменится только класс", "появится новый класс", "удалится поле класса"]),
            )
        else:
            questions = (
                (f"Что вернет add{number}(value, step=2), если функция возвращает value + step и вызвана как add{number}({number})?", number + 2, [number, number + 1, number + 3]),
                (f"Какой результат даст multiply{number}(a={number}, b=3), если внутри возвращается a * b?", number * 3, [number + 3, number * 2, number]),
                (f"Что вернет sum{number}(*args), если она возвращает sum(args), при аргументах {number}, {number + 1}, {number + 2}?", number * 3 + 3, [number * 3, number + 2, number + 1]),
                (f"Какой результат у lambda x: x ** 2 + {number} при x={number}?", number ** 2 + number, [number ** 2, number * 2, number + 2]),
                (f"Что вернет empty{number}(), если в теле функции нет оператора return?", None, [0, False, ""]),
                (f"Что вернет функция first{number}(items), выполняющая return items[0], для ['{word}', 'end']?", word, ["end", ["end"], None]),
                (f"Какое значение получит аргумент label при вызове show{number}(label='{word}')?", word, ["label", None, number]),
                (f"Какой объект возвращает функция make{number}(), если в ней написано return {{'id': {number}}}?", {"id": number}, [number, [number], None]),
                (f"Какой результат даст max{number}(a, b), если функция возвращает a if a > b else b и получает ({number}, {number + 1})?", number + 1, [number, number - 1, None]),
                (f"Сколько значений вернет функция pair{number}(), выполняющая return {number}, '{word}'?", 2, [1, 3, None]),
                (f"Какой результат вернет function{number}(value, *, scale=2), вызванная как function{number}({number}, scale=3), если она возвращает value * scale?", number * 3, [number * 2, number + 3, number]),
                (f"Какое значение сохранит **kwargs при вызове options{number}(color='{word}')?", {"color": word}, [word, ["color", word], None]),
                (f"Чему равно sum(map(lambda value: value + 1, [{number}, {number + 1}]))?", number + number + 3, [number + number + 1, number + number + 2, number]),
                (f"Сколько элементов оставит list(filter(lambda value: value > {number}, [1, {number}, {number + 1}, {number + 2}]))?", 2, [1, 3, 4]),
                (f"Что вернет recursive{number}(1), если функция возвращает 1 при n == 0, иначе recursive{number}(n - 1) + 1?", 2, [1, 3, 0]),
                (f"Какое значение вернет choose{number}(x), если внутри функции присвоить x = {number + 1}, а затем вернуть x?", number + 1, [number, 0, None]),
                (f"Какие значения передаются в параметр *args функции collect{number}({number}, '{word}', True)?", (number, word, True), [(number, word), (word, True), [number, word, True]]),
                (f"Что вернет функция doubled{number}(), если она возвращает [value * 2 for value in range(2)]?", [0, 2], [0, [2], [0, 1]]),
                (f"Сколько аргументов получит wrapper{number}(*args, **kwargs) при вызове wrapper{number}({number}, mode='{word}')?", 2, [1, 3, 0]),
                (f"Какое значение вернет is_positive{number}({number}), если функция выполняет return value > 0?", True, [False, number, None]),
            )

        for title, correct, wrong in questions:
            add(title, correct, wrong)

    return candidates


def python_concept_question_candidates(
    subject: Subject,
    difficulty: Difficulty,
) -> list[tuple[str, str, list[str]]]:
    if subject in PYTHON_BASICS_TOPIC_SUBJECTS:
        return python_basics_topic_candidates(subject, difficulty)

    if subject == Subject.PYTHON_BASES:
        words = (
            "python", "string", "loop", "array", "object", "function",
            "condition", "integer", "boolean", "dictionary", "tuple",
            "method", "inheritance", "variable", "iteration", "return",
            "mutable", "sequence", "keyword", "attribute",
        )
        candidates = []

        def make_candidate(title: str, correct: object, wrong: list[object]) -> None:
            correct_answer = repr(correct)
            wrong_answers = list(dict.fromkeys(repr(option) for option in wrong if repr(option) != correct_answer))
            while len(wrong_answers) < 3:
                wrong_answers.append(repr(f"неверный ответ {len(wrong_answers) + 1}"))
            candidates.append((title, correct_answer, wrong_answers[:3]))

        for number, word in enumerate(words, 1):
            values = (number, number + 1, number + 2)
            if subject == Subject.PYTHON_CONDITIONS:
                conditions = (
                    (f"{number} > 10", number > 10),
                    (f"{number} % 2 == 0", number % 2 == 0),
                    (f"{number} >= 5 and {number} < 15", 5 <= number < 15),
                    (f"{number} % 3 == 0 or {number} % 5 == 0", number % 3 == 0 or number % 5 == 0),
                    (f"not ({number} < 10)", not number < 10),
                )
                for expression, result in conditions:
                    make_candidate(
                        f"Какое значение примет условие if {expression}?",
                        result,
                        [not result, number, None],
                    )
            elif subject == Subject.PYTHON_STRINGS:
                string_operations = (
                    (f"{word}[0]", word[0]),
                    (f"{word}[-1]", word[-1]),
                    (f"{word}.upper()", word.upper()),
                    (f"{word}[::-1]", word[::-1]),
                    (f"{word}[:len({word}) // 2]", word[:len(word) // 2]),
                )
                for expression, result in string_operations:
                    make_candidate(
                        f"Что вернет выражение {expression} для строки {word!r}?",
                        result,
                        [word.upper(), word.capitalize(), word[::-1], f"{word}!"],
                    )
            elif subject == Subject.PYTHON_DATA_STRUCTURES:
                data_questions = (
                    (
                        f"Что вернет values[0] для списка values = {list(values)}?",
                        values[0],
                        [values[1], values[2], len(values)],
                    ),
                    (
                        f"Что вернет pair[1] для кортежа pair = ({number}, {number + 1})?",
                        number + 1,
                        [number, number + 2, 0],
                    ),
                    (
                        f"Что вернет record.get('score') для словаря record = {{'id': {number}, 'score': {number * 10}}}?",
                        number * 10,
                        [number * 10 + 1, number * 10 + 2, number * 10 + 3],
                    ),
                    (
                        f"Чему равен результат sorted(set([{number}, {number + 1}, {number}]))?",
                        [number, number + 1],
                        [[number, number + 1, number], [number + 1, number], [number]],
                    ),
                    (
                        f"Сколько элементов будет в values после values.append({number + 3}), если изначально values = {list(values)}?",
                        4,
                        [3, 5, number + 3],
                    ),
                )
                for title, result, wrong in data_questions:
                    make_candidate(title, result, wrong)
            elif subject == Subject.PYTHON_LOOPS:
                loop_questions = (
                    (f"Чему равна сумма чисел в цикле for value in range({number})?", sum(range(number))),
                    (
                        f"Какой список получится после отбора нечетных чисел циклом из range({number})?",
                        [value for value in range(number) if value % 2],
                    ),
                    (
                        f"Сколько чисел от 1 до {number} включительно делятся на 3?",
                        sum(1 for value in range(1, number + 1) if value % 3 == 0),
                    ),
                    (
                        f"Чему равна сумма квадратов чисел от 1 до {number} включительно?",
                        sum(value ** 2 for value in range(1, number + 1)),
                    ),
                    (
                        f"Сколько итераций выполнит цикл while count < {number}, если count начинается с 0 и увеличивается на 1?",
                        number,
                    ),
                )
                for index, item in enumerate(loop_questions):
                    title, result = item
                    if index == 1:
                        wrong = [
                            [value for value in range(number) if value % 2 == 0],
                            list(range(number)),
                            list(range(1, number)),
                        ]
                    else:
                        wrong = [result + 1, result + 2, result + 3]
                    make_candidate(title, result, wrong)
            elif subject == Subject.PYTHON_OOP:
                oop_questions = (
                    (
                        f"В классе Counter{number} метод __init__ сохраняет {number} в self.value, а increase(step) прибавляет step. Чему равно value после increase(1)?",
                        number + 1,
                    ),
                    (
                        f"Чему равно isinstance(Child{number}(), Parent), если Child{number} наследуется от Parent?",
                        True,
                    ),
                    (
                        f"Что вернет объект класса Child{number}, если он переопределяет метод value() из Parent и возвращает {number + 2}?",
                        number + 2,
                    ),
                    (
                        f"У класса Box{number} метод __init__ сохраняет width={number} и height={number + 1}. Чему равно width * height?",
                        number * (number + 1),
                    ),
                    (
                        f"Что вернет метод add{number}, который увеличивает self.total на {number} и возвращает self.total, если total изначально равен 0?",
                        number,
                    ),
                )
                for title, result in oop_questions:
                    make_candidate(title, result, [result + 1, result + 2, result + 3] if isinstance(result, int) else [False, None, "Child"])
            else:
                function_questions = (
                    (
                        f"Что вернет функция calculate{number}(value, extra=2), возвращающая value + extra, при вызове calculate{number}({number})?",
                        number + 2,
                    ),
                    (
                        f"Чему равно calculate{number}(a={number}, b={number + 1}), если функция возвращает a * b?",
                        number * (number + 1),
                    ),
                    (
                        f"Что вернет функция total{number}(*args), вычисляющая sum(args), при вызове total{number}({number}, {number + 1}, {number + 2})?",
                        sum(values),
                    ),
                    (
                        f"Что вернет функция square{number}(x), возвращающая x ** 2 + {number}, если вызвать ее с x={number}?",
                        number ** 2 + number,
                    ),
                    (
                        f"Какое значение вернет lambda x: x * {number} при передаче аргумента 2?",
                        number * 2,
                    ),
                )
                for title, result in function_questions:
                    make_candidate(title, result, [result + 1, result + 2, result + 3])

        return candidates

    if subject == Subject.PYTHON_BASES:
        words = (
            "python", "string", "loop", "array", "object", "function",
            "condition", "integer", "boolean", "dictionary", "tuple",
            "method", "inheritance", "variable", "iteration", "return",
            "mutable", "sequence", "keyword", "attribute",
        )
        candidates = []

        for number, word in enumerate(words, 1):
            if difficulty == Difficulty.EASY:
                condition = f"{number} > 10"
                condition_result = number > 10
                string_expression = f"{word}[0]"
                string_result = word[0]
                list_expression = "values[0]"
                list_result = number
                loop_expression = f"sum(range({number}))"
                loop_result = sum(range(number))
            elif difficulty == Difficulty.MEDIUM:
                condition = f"{number} % 2 == 0"
                condition_result = number % 2 == 0
                string_expression = f"{word}[-1]"
                string_result = word[-1]
                list_expression = "values[-1]"
                list_result = number + 2
                loop_expression = f"sum(range(1, {number + 1}))"
                loop_result = sum(range(1, number + 1))
            elif difficulty == Difficulty.HARD:
                condition = f"{number} > 5 and {number} < 15"
                condition_result = number > 5 and number < 15
                string_expression = f"{word}.upper()"
                string_result = word.upper()
                list_expression = "values[1:]"
                list_result = [number + 1, number + 2]
                loop_expression = f"sum(value for value in range({number + 1}) if value % 2 == 0)"
                loop_result = sum(value for value in range(number + 1) if value % 2 == 0)
            elif difficulty == Difficulty.ADVANCED:
                condition = f"{number} % 3 == 0 or {number} % 5 == 0"
                condition_result = number % 3 == 0 or number % 5 == 0
                string_expression = f"{word}[:len({word}) // 2]"
                string_result = word[:len(word) // 2]
                list_expression = "values + [number * 2]"
                list_result = [number, number + 1, number + 2, number * 2]
                loop_expression = f"sum(value for value in range(1, {number + 1}) if value % 3 == 0)"
                loop_result = sum(value for value in range(1, number + 1) if value % 3 == 0)
            else:
                condition = f"{number} % 2 == 0 and {number} % 3 == 0"
                condition_result = number % 2 == 0 and number % 3 == 0
                string_expression = f"{word}[::-1]"
                string_result = word[::-1]
                list_expression = "[value for value in values if value % 2 == 0]"
                list_result = [value for value in (number, number + 1, number + 2) if value % 2 == 0]
                loop_expression = f"sum(value ** 2 for value in range(1, {number + 1}))"
                loop_result = sum(value ** 2 for value in range(1, number + 1))

            candidates.extend([
                (
                    f"Какое значение примет условие if {condition}?",
                    str(condition_result),
                    ["False", "True", "None", "0"],
                ),
                (
                    f"Что вернет выражение {string_expression} для строки {word!r}?",
                    repr(string_result),
                    [repr(word.upper()), repr(word.capitalize()), repr(word[::-1]), repr(f"{word}!")],
                ),
                (
                    f"Что вернет выражение {list_expression} для values = [{number}, {number + 1}, {number + 2}]?",
                    repr(list_result),
                    [
                        repr([number + 1, number + 2]),
                        repr([number, number + 1]),
                        repr([number + 2, number + 1, number]),
                        repr([number, number + 1, number + 2, number + 3]),
                    ],
                ),
                (
                    f"Какое значение вернет record.get('score') для record = {{'id': {number}, 'score': {number * 10}}}?",
                    str(number * 10),
                    [str(number * 10 + offset) for offset in (1, 2, 3, 4)],
                ),
                (
                    f"Чему будет равен total после выполнения {loop_expression}?",
                    str(loop_result),
                    [str(loop_result + offset) for offset in (1, 2, 3, 4)],
                ),
                (
                    f"В классе Counter{number} метод __init__ сохраняет {number} в self.value, а метод increase(step) увеличивает self.value на step. Чему будет равно counter.value после counter.increase({number % 5 + 1})?",
                    str(number + number % 5 + 1),
                    [str(number + number % 5 + 1 + offset) for offset in (1, 2, 3, 4)],
                ),
                (
                    f"Что вернет функция calculate{number}(value), если она возвращает value * {number % 4 + 2} + {number} и вызвать ее с value = {number}?",
                    str(number * (number % 4 + 2) + number),
                    [
                        str(number * (number % 4 + 2) + number + offset)
                        for offset in (1, 2, 3, 4)
                    ],
                ),
            ])

        return [
            (
                title,
                correct,
                [option for option in dict.fromkeys(wrong) if option != correct][:3],
            )
            for title, correct, wrong in candidates
        ]

    if subject == Subject.DATABASES:
        tables = (
            "users", "orders", "products", "customers", "employees",
            "payments", "invoices", "events", "sessions", "messages",
            "inventory", "categories", "reviews", "shipments", "accounts",
            "departments", "projects", "tasks", "logs", "subscriptions",
        )
        templates = {
            Difficulty.EASY: (
                ("Как выбрать все столбцы из таблицы {table}?",
                 "SELECT * FROM {table};",
                 ["GET * FROM {table};", "SELECT ALL {table};", "FETCH * IN {table};"]),
                ("Как отфильтровать строки таблицы {table} по условию WHERE?",
                 "SELECT * FROM {table} WHERE id = 1;",
                 ["SELECT * FROM {table} FILTER id = 1;", "SELECT WHERE id = 1 FROM {table};", "GET * FROM {table} IF id = 1;"]),
                ("Как добавить новую строку в таблицу {table}?",
                 "INSERT INTO {table} (name) VALUES ('value');",
                 ["ADD INTO {table} (name) VALUE ('value');", "INSERT {table} SET name = 'value';", "CREATE ROW IN {table} (name = 'value');"]),
                ("Как изменить значение столбца status в таблице {table}?",
                 "UPDATE {table} SET status = 'active' WHERE id = 1;",
                 ["MODIFY {table} SET status = 'active' WHERE id = 1;", "UPDATE {table} status = 'active' IF id = 1;", "ALTER {table} SET status = 'active';"]),
                ("Как удалить из таблицы {table} только строку с id = 1?",
                 "DELETE FROM {table} WHERE id = 1;",
                 ["DROP ROW FROM {table} WHERE id = 1;", "DELETE TABLE {table} WHERE id = 1;", "REMOVE FROM {table} id = 1;"]),
            ),
            Difficulty.MEDIUM: (
                ("Как отсортировать строки таблицы {table} по created_at от новых к старым?",
                 "SELECT * FROM {table} ORDER BY created_at DESC;",
                 ["SELECT * FROM {table} SORT BY created_at DESC;", "SELECT * FROM {table} ORDER created_at NEWEST;", "SELECT * FROM {table} GROUP BY created_at DESC;"]),
                ("Как получить первые 10 строк таблицы {table} в PostgreSQL или MySQL?",
                 "SELECT * FROM {table} LIMIT 10;",
                 ["SELECT TOP 10 * FROM {table};", "SELECT * FROM {table} FIRST 10;", "SELECT * FROM {table} FETCH 10 ONLY;"]),
                ("Как посчитать количество строк в таблице {table}?",
                 "SELECT COUNT(*) FROM {table};",
                 ["SELECT SIZE(*) FROM {table};", "COUNT ROWS IN {table};", "SELECT LENGTH(*) FROM {table};"]),
                ("Как объединить строки {table} с таблицей departments по совпадающему department_id?",
                 "SELECT * FROM {table} JOIN departments ON {table}.department_id = departments.id;",
                 ["SELECT * FROM {table} MERGE departments BY department_id;", "SELECT * FROM {table} LINK departments USING department_id;", "SELECT * FROM {table} JOIN departments WHERE department_id;"]),
                ("Как сгруппировать строки таблицы {table} по status и посчитать строки в каждой группе?",
                 "SELECT status, COUNT(*) FROM {table} GROUP BY status;",
                 ["SELECT status, COUNT(*) FROM {table} ORDER BY status;", "SELECT status, COUNT(*) FROM {table} PARTITION status;", "SELECT GROUP(status), COUNT(*) FROM {table};"]),
            ),
            Difficulty.HARD: (
                ("Как создать индекс для ускорения поиска по email в таблице {table}?",
                 "CREATE INDEX idx_{table}_email ON {table} (email);",
                 ["ADD INDEX idx_{table}_email TO {table} (email);", "CREATE TABLE INDEX idx_{table}_email FOR email;", "INDEX {table}.email AS idx_{table}_email;"]),
                ("Как безопасно передать пользовательское значение в SQL-запрос к таблице {table} из Python?",
                 "Использовать параметризованный запрос и передать значение отдельно от SQL-текста",
                 ["Вставить значение в SQL через f-строку", "Склеить SQL и значение оператором +", "Удалить одинарные кавычки из значения и склеить запрос"]),
                ("Как выбрать строки таблицы {table}, для которых нет связанной строки в таблице details?",
                 "Использовать LEFT JOIN details и отфильтровать строки, где details.id IS NULL",
                 ["Использовать INNER JOIN и фильтр details.id IS NULL", "Использовать CROSS JOIN без условия", "Использовать UNION без проверки наличия details"]),
                ("Как атомарно выполнить несколько изменений таблицы {table}?",
                 "Выполнить изменения в транзакции и зафиксировать их одним COMMIT",
                 ["Выполнить каждый запрос с autocommit и считать их одной транзакцией", "Выполнить запросы в SELECT", "Объединить запросы строкой без транзакции"]),
                ("Что означает ограничение FOREIGN KEY для столбца owner_id таблицы {table}?",
                 "Оно обеспечивает ссылочную целостность относительно ключа связанной таблицы",
                 ["Оно автоматически шифрует owner_id", "Оно гарантирует уникальность всех строк таблицы", "Оно создаёт резервную копию связанной таблицы"]),
            ),
            Difficulty.ADVANCED: (
                ("Какой тип PostgreSQL подходит для хранения и индексируемого поиска по JSON-структурам в таблице {table}?",
                 "jsonb",
                 ["xmlb", "jsontext", "blobjson"]),
                ("Как SQLite хранит значение типа INTEGER PRIMARY KEY в таблице {table}?",
                 "Как псевдоним внутреннего ROWID таблицы, кроме таблиц WITHOUT ROWID",
                 ["Всегда как UUID, генерируемый сервером", "Как внешний ключ на sqlite_sequence во всех таблицах", "Только как обычный текстовый столбец без связи с ROWID"]),
                ("Какой движок MySQL поддерживает транзакции и внешние ключи для таблицы {table}?",
                 "InnoDB",
                 ["MyISAM", "MEMORY", "CSV"]),
                ("Что обычно следует сделать после успешных изменений таблицы {table} в явной транзакции?",
                 "Выполнить COMMIT, чтобы зафиксировать изменения",
                 ["Выполнить ROLLBACK, чтобы сохранить изменения", "Выполнить EXPLAIN, чтобы зафиксировать изменения", "Закрыть соединение без commit и ожидать сохранения во всех драйверах"]),
                ("Как сравниваются NULL-значения при проверке столбца owner_id таблицы {table}?",
                 "Использовать IS NULL или IS NOT NULL, а не сравнение через =",
                 ["Использовать owner_id = NULL", "Использовать owner_id == NULL во всех SQL-диалектах", "Сравнивать NULL только оператором LIKE"]),
            ),
            Difficulty.EXPERT: (
                ("Какой уровень изоляции транзакций гарантирует сериализуемое поведение операций над таблицей {table}?",
                 "SERIALIZABLE",
                 ["READ UNCOMMITTED всегда гарантирует сериализуемость", "AUTOCOMMIT", "NOLOCK"]),
                ("Почему повтор команды UPDATE для таблицы {table} может быть небезопасен без идемпотентного условия?",
                 "Повтор может повторно изменить уже обработанные строки или применить изменение второй раз",
                 ["SQL запрещает повторять UPDATE в транзакции", "Повтор автоматически удаляет таблицу", "UPDATE при повторе всегда преобразуется в SELECT"]),
                ("Как снизить риск потери обновлений при конкурентном изменении одной строки {table}?",
                 "Использовать подходящую блокировку строки или оптимистическую проверку версии в транзакции",
                 ["Отключить транзакции для всех соединений", "Сначала читать строку без блокировки и всегда перезаписывать её", "Использовать SELECT DISTINCT перед UPDATE"]),
                ("Почему план выполнения запроса к таблице {table} может измениться после обновления статистики?",
                 "Оптимизатор использует статистику распределения данных для выбора плана",
                 ["Статистика автоматически меняет текст SQL-запроса", "Обновление статистики всегда удаляет индексы", "План выполнения хранится только внутри клиентского драйвера"]),
                ("Как безопасно выполнять миграцию схемы таблицы {table} при развёртывании приложения?",
                 "Версионировать миграции, применять их контролируемо и учитывать совместимость старой и новой версии приложения",
                 ["Редактировать схему вручную на каждом сервере без журнала", "Удалить таблицу и пересоздать её при каждом старте", "Менять схему только через SELECT-запрос приложения"]),
            ),
        }
        return [
            (
                title.format(table=table),
                correct.format(table=table),
                [wrong.format(table=table) for wrong in wrong_answers],
            )
            for title, correct, wrong_answers in templates[difficulty]
            for table in tables
        ]

    if subject == Subject.FILES_FORMATS:
        files = (
            "notes", "settings", "user_records", "monthly_report",
            "application_config", "defaults", "server_settings", "event_log",
            "image_data", "archive_data", "record_stream", "tabular_data",
            "project_readme", "user_store", "data_export", "private_settings",
            "product_catalog", "service_defaults", "messages", "backup_data",
        )
        templates = {
            Difficulty.EASY: (
                ("Как открыть {file} для чтения текста с явной кодировкой UTF-8?",
                 "with open('{file}', 'r', encoding='utf-8') as file_obj:",
                 ["with open('{file}', 'w', encoding='utf-8') as file_obj:", "open('{file}', 'rb', encoding='utf-8')", "read('{file}', encoding='utf-8')"]),
                ("Как гарантированно закрыть открытый файл {file} даже при исключении?",
                 "Использовать конструкцию with open(...) as file_obj:",
                 ["Вызвать file_obj.close() только в конце функции", "Открыть файл без контекстного менеджера", "Удалить переменную file_obj после чтения"]),
                ("Как создать объект пути к файлу {file} с помощью pathlib?",
                 "Path('{file}')",
                 ["FilePath('{file}')", "os.Path('{file}')", "pathlib.open('{file}')"]),
                ("Как прочитать содержимое текстового файла {file} методом pathlib?",
                 "Path('{file}').read_text(encoding='utf-8')",
                 ["Path('{file}').read_bytes(encoding='utf-8')", "Path('{file}').open_text(encoding='utf-8')", "Path.read('{file}')"]),
                ("Как записать текст в {file}, заменив его прежнее содержимое?",
                 "Path('{file}').write_text(text, encoding='utf-8')",
                 ["Path('{file}').read_text(text)", "Path('{file}').write_bytes(text, encoding='utf-8')", "Path('{file}').append_text(text)"]),
            ),
            Difficulty.MEDIUM: (
                ("Как прочитать строки CSV-файла {file} как словари по заголовкам столбцов?",
                 "Использовать csv.DictReader(file_obj)",
                 ["Использовать json.load(file_obj)", "Использовать csv.DictWriter(file_obj)", "Использовать csv.DictReader('{file}') без открытия файла"]),
                ("Как открыть CSV-файл {file} для записи с рекомендуемым параметром newline=''?",
                 "open('{file}', 'w', newline='', encoding='utf-8')",
                 ["open('{file}', 'r', newline='\\n', encoding='utf-8')", "open('{file}', 'wb', newline='', encoding='utf-8')", "open('{file}', 'a+', newline='csv', encoding='utf-8')"]),
                ("Как загрузить один JSON-документ из уже открытого файла {file}?",
                 "json.load(file_obj)",
                 ["json.loads(file_obj)", "json.dump(file_obj)", "json.read(file_obj)"]),
                ("Чем json.loads(text) отличается от json.load(file_obj) при разборе {file}?",
                 "loads разбирает строку, а load читает JSON из файлового объекта",
                 ["loads читает файловый объект, а load принимает только строку", "loads записывает JSON, а load сортирует ключи", "Эти функции предназначены только для бинарных файлов"]),
                ("Как проверить, существует ли путь {file} перед чтением?",
                 "Path('{file}').exists()",
                 ["Path('{file}').is_open()", "os.file_exists('{file}')", "open('{file}').exists()"]),
            ),
            Difficulty.HARD: (
                ("Как открыть бинарный файл {file} для чтения байтов?",
                 "open('{file}', 'rb')",
                 ["open('{file}', 'r', encoding='utf-8')", "open('{file}', 'wb', encoding='utf-8')", "Path('{file}').read_text(mode='rb')"]),
                ("Как прочитать TSV-файл {file} стандартным модулем csv?",
                 "Передать csv.reader файловый объект с delimiter='\\t'",
                 ["Передать json.load файловый объект с delimiter='\\t'", "Использовать csv.reader('{file}', separator='\\t')", "Передать csv.writer параметр encoding='tsv'"]),
                ("Как записать последовательность объектов в формате JSON Lines в {file}?",
                 "Записать каждый объект отдельной строкой через json.dumps(obj) и '\\n'",
                 ["Сериализовать весь список одним вызовом json.dumps без разделителей строк", "Вызвать json.load для каждого объекта при записи", "Разделить объекты символом запятой без перевода строки"]),
                ("Почему для большого файла {file} предпочтительно обрабатывать строки в цикле?",
                 "Это позволяет не загружать всё содержимое файла в память одновременно",
                 ["Итерация по строкам автоматически сжимает файл", "Чтение строк отключает проверку кодировки", "Метод read() всегда возвращает только одну строку"]),
                ("Как добавить строку в конец текстового файла {file}, не стирая существующее содержимое?",
                 "Открыть файл в режиме 'a' и записать строку",
                 ["Открыть файл в режиме 'w' и записать строку", "Открыть файл в режиме 'x' и записать строку", "Открыть файл в режиме 'r' и вызвать write"]),
            ),
            Difficulty.ADVANCED: (
                ("Как разобрать TOML-файл {file} стандартной библиотекой в Python 3.11+?",
                 "Использовать tomllib.load() с файлом, открытым в бинарном режиме 'rb'",
                 ["Использовать tomllib.loads() с файловым объектом в режиме 'r'", "Вызвать tomllib.dump() для чтения", "Импортировать toml из стандартной библиотеки Python 3.11"]),
                ("Как загрузить YAML-файл {file} безопасным способом с PyYAML?",
                 "yaml.safe_load(file_obj)",
                 ["yaml.load(file_obj) без указания Loader", "yaml.unsafe_load(file_obj)", "json.load(file_obj)"]),
                ("Какой модуль стандартной библиотеки подходит для разбора XML-файла {file}?",
                 "xml.etree.ElementTree",
                 ["csv.etree.ElementTree", "configparser", "tomllib"]),
                ("Как прочитать секции и параметры INI-файла {file} стандартной библиотекой?",
                 "Создать configparser.ConfigParser и вызвать read()",
                 ["Создать json.JSONDecoder и вызвать read()", "Вызвать csv.DictReader.read()", "Использовать pathlib.ConfigParser.read_text()"]),
                ("Как сохранить JSON в файл {file} непосредственно из Python-объекта?",
                 "json.dump(data, file_obj, ensure_ascii=False, indent=2)",
                 ["json.loads(data, file_obj)", "json.load(data, file_obj)", "json.dumps(file_obj, data)"]),
            ),
            Difficulty.EXPERT: (
                ("Почему нельзя загружать недоверенный файл {file} с помощью pickle.load()?",
                 "Десериализация pickle может выполнить произвольный код",
                 ["pickle.load всегда удаляет исходный файл", "Формат pickle не поддерживает числа и строки", "pickle.load автоматически отправляет данные в сеть"]),
                ("Как безопаснее заменить файл {file} новым содержимым без частично записанного результата?",
                 "Записать временный файл в той же файловой системе и затем выполнить os.replace()",
                 ["Сначала удалить исходный файл и затем писать напрямую", "Записать половину содержимого, вызвать flush и переименовать открытый файл", "Открыть исходный файл в режиме 'a' и перезаписать начало"]),
                ("Как снизить риск path traversal при обработке пользовательского имени файла {file}?",
                 "Разрешить путь относительно заданного корня и проверить, что итоговый путь остаётся внутри этого корня",
                 ["Склеить пользовательский ввод с корнем через строковый '+'", "Заменить только первый символ '..'", "Проверить, что имя содержит точку, и открыть его"]),
                ("Как защитить экспорт в CSV-файл {file} от формул, вводимых пользователем?",
                 "Экранировать или нейтрализовать значения, начинающиеся с символов, интерпретируемых таблицами как формулы",
                 ["Удалить все запятые из CSV", "Открывать файл только в бинарном режиме", "Сериализовать каждую ячейку через pickle"]),
                ("Как проверить структуру данных из JSON-файла {file} после синтаксического разбора?",
                 "Отдельно проверить типы, обязательные поля и ограничения схемы приложения",
                 ["Считать успешный json.load подтверждением корректности бизнес-схемы", "Проверить только расширение файла", "Вызвать json.dumps и считать отсутствие исключения проверкой схемы"]),
            ),
        }
        return [
            (
                title.format(file=file),
                correct.format(file=file),
                [wrong.format(file=file) for wrong in wrong_answers],
            )
            for title, correct, wrong_answers in templates[difficulty]
            for file in files
        ]

    if subject == Subject.LINTERS:
        files = (
            "app.py", "models.py", "routes.py", "settings.py", "database.py",
            "helpers.py", "cli.py", "worker.py", "parser.py", "service.py",
            "tests/test_api.py", "tests/test_models.py", "src/client.py",
            "src/storage.py", "src/validators.py", "scripts/import_data.py",
            "package/__init__.py", "package/handlers.py", "package/tasks.py",
            "tools/check_config.py",
        )
        templates = {
            Difficulty.EASY: (
                ("Как проверить файл {file} линтером Ruff?",
                 "ruff check {file}",
                 ["ruff format {file}", "python ruff {file}", "ruff test {file}"]),
                ("Какова основная задача линтера при проверке {file}?",
                 "Находить нарушения правил и вероятные ошибки в исходном коде",
                 ["Запускать программу и проверять её результат", "Автоматически исправлять все ошибки без анализа", "Устанавливать зависимости проекта"]),
                ("Как запустить Flake8 для файла {file}?",
                 "flake8 {file}",
                 ["flake8 --format {file}", "python -m flake8.run {file}", "flake8 test {file}"]),
                ("Как вызвать Pylint для проверки {file} из командной строки?",
                 "pylint {file}",
                 ["pylint --format-only {file}", "python -m pylint.run {file}", "pylint test {file}"]),
                ("В какой секции pyproject.toml задают основные настройки Ruff для {file}?",
                 "[tool.ruff]",
                 ["[ruff]", "[tool.flake8]", "[project.ruff]"]),
            ),
            Difficulty.MEDIUM: (
                ("Как сообщить Ruff о неиспользуемом импорте в {file} с помощью кода F401?",
                 "ruff check --select F401 {file}",
                 ["ruff format --select F401 {file}", "ruff check --ignore F401 {file}", "python -m ruff F401 {file}"]),
                ("Как подавить предупреждение E501 только для строки в {file} при использовании Flake8?",
                 "Добавить к этой строке комментарий # noqa: E501",
                 ["Добавить к строке комментарий # flake8: off", "Поставить перед строкой # noqa: E501: off", "Добавить в конец строки noqa = E501"]),
                ("Как попросить Ruff автоматически применить доступные безопасные исправления в {file}?",
                 "ruff check --fix {file}",
                 ["ruff format --fix {file}", "ruff check --unsafe {file}", "ruff autofix {file}"]),
                ("Как отключить конкретное сообщение Pylint только в файле {file}?",
                 "Добавить директиву # pylint: disable=имя-сообщения",
                 ["Добавить директиву # noqa: pylint", "Добавить директиву # pylint: ignore-all", "Передать комментарий # disable pylint"]),
                ("Как проверить все Python-файлы каталога tests, включая {file}, командой Flake8?",
                 "flake8 tests/",
                 ["flake8 --recursive tests/", "python -m flake8 tests/*.py --run", "flake8 --directory tests/"]),
            ),
            Difficulty.HARD: (
                ("Как в конфигурации Ruff включить семейства правил pycodestyle и Pyflakes для {file}?",
                 "Задать select = ['E', 'F'] в секции [tool.ruff.lint]",
                 ["Задать ignore = ['E', 'F'] в секции [tool.ruff.lint]", "Задать select = ['W', 'I'] в секции [tool.ruff.format]", "Добавить E и F в target-version"]),
                ("Как указать Ruff пропускать каталог generated при проверке {file}?",
                 "Добавить каталог в extend-exclude в конфигурации Ruff",
                 ["Добавить каталог в select", "Указать каталог в target-version", "Включить каталог в line-length"]),
                ("Что позволяет CI определить по коду завершения Ruff при проверке {file}?",
                 "Есть ли нарушения, из-за которых проверка должна считаться неуспешной",
                 ["Сколько строк кода было автоматически отформатировано", "Установлены ли все зависимости проекта", "Прошли ли runtime-тесты приложения"]),
                ("Как задать исключения Flake8 только для файла {file} в конфигурации?",
                 "Использовать параметр per-file-ignores с именем файла и кодами правил",
                 ["Добавить файл в max-line-length", "Использовать select с путем к файлу", "Задать target-version для каждого файла"]),
                ("Как подавить Ruff-правило только на одной строке файла {file}?",
                 "Добавить комментарий # noqa: CODE с кодом конкретного правила",
                 ["Добавить комментарий # noqa без кода и на следующую строку", "Добавить # ruff: ignore-file в конец строки", "Изменить глобальный select в pyproject.toml"]),
            ),
            Difficulty.ADVANCED: (
                ("Как отличить проверку Ruff от форматирования при работе с {file}?",
                 "ruff check анализирует lint-правила, а ruff format форматирует код",
                 ["ruff check форматирует код, а ruff format запускает тесты", "Обе команды выполняют только статическую проверку типов", "ruff format проверяет правила, а ruff check меняет отступы"]),
                ("Как включить правила сортировки импортов Ruff для {file}?",
                 "Добавить код правила I в select секции [tool.ruff.lint]",
                 ["Добавить I в target-version", "Включить I через секцию [tool.ruff.format]", "Запустить ruff check --sort-imports"]),
                ("Как подключить отдельные хуки Ruff и форматирования Ruff в pre-commit для {file}?",
                 "Настроить соответствующие hook id ruff и ruff-format",
                 ["Задать hook id flake8-format для обеих операций", "Добавить команду ruff test в pre-commit", "Использовать один hook id ruff-format для линтинга и тестов"]),
                ("Почему для {file} обычно безопаснее отключить одно конкретное правило, а не все правила линтера?",
                 "Локальное исключение сохраняет остальные проверки и уменьшает риск пропустить ошибки",
                 ["Глобальное отключение ускоряет линтер, но делает его точнее", "Отключение одного правила автоматически форматирует файл", "Локальные исключения обязательны для запуска Python"]),
                ("Как настройка target-version в Ruff влияет на проверку {file}?",
                 "Сообщает линтеру целевую версию Python для выбора применимых правил",
                 ["Устанавливает указанную версию Python в окружение", "Задаёт максимальную длину строк", "Выбирает каталог для поиска тестов"]),
            ),
            Difficulty.EXPERT: (
                ("Как обнаружить неиспользуемые директивы noqa в {file} с помощью Ruff?",
                 "Включить правило RUF100",
                 ["Включить правило F401 только для форматирования", "Запустить ruff format --check-noqa", "Добавить noqa в target-version"]),
                ("Как обеспечить одинаковый результат линтинга {file} локально и в CI?",
                 "Зафиксировать версии инструментов и использовать одну и ту же конфигурацию и команды",
                 ["Использовать разные конфигурации, чтобы сравнить результаты", "Запускать линтер только локально и переносить его вывод в CI", "Обновлять инструменты до случайной версии при каждом запуске"]),
                ("Как mypy отличается от Ruff при проверке {file}?",
                 "mypy проверяет статическую согласованность типов, Ruff применяет lint-правила и может форматировать код",
                 ["mypy форматирует импорты, а Ruff выполняет типизацию во время запуска", "Оба инструмента являются только runtime-тестировщиками", "Ruff компилирует приложение, а mypy устанавливает зависимости"]),
                ("Как настроить Pylint, чтобы проверка {file} считалась успешной только при заданной минимальной оценке?",
                 "Передать параметр --fail-under с требуемым порогом",
                 ["Передать параметр --min-score только в Ruff", "Задать --line-length как порог оценки", "Использовать --exit-zero при запуске Pylint"]),
                ("Как получить машинно-читаемый JSON-отчет Ruff о проверке {file}?",
                 "Запустить ruff check --output-format json {file}",
                 ["Запустить ruff format --output json {file}", "Запустить ruff check --json-only {file}", "Добавить output-format = json в секцию [project]"]),
            ),
        }
        return [
            (
                title.format(file=file),
                correct.format(file=file),
                [wrong.format(file=file) for wrong in wrong_answers],
            )
            for title, correct, wrong_answers in templates[difficulty]
            for file in files
        ]

    if subject in (Subject.UNITTEST, Subject.PYTEST):
        names = (
            "calculate_total", "parse_user", "format_date", "validate_email",
            "load_config", "create_order", "normalize_text", "convert_price",
            "find_user", "build_report", "is_valid", "get_items",
            "save_record", "sum_values", "make_slug", "read_data",
            "check_status", "format_name", "filter_rows", "compute_score",
        )
        if subject == Subject.UNITTEST:
            templates = {
                Difficulty.EASY: (
                    ("Как импортировать TestCase для теста функции {name}?",
                     "from unittest import TestCase",
                     ["from unittest import Test", "import TestCase from unittest", "from unittest.test import TestCase"]),
                    ("От какого класса обычно наследуют тестовый класс для {name}?",
                     "unittest.TestCase",
                     ["unittest.Test", "unittest.TestSuite", "unittest.TestRunner"]),
                    ("Какой префикс должен иметь метод тестирования {name} в unittest?",
                     "test_",
                     ["check_", "spec_", "unittest_"]),
                    ("Как запустить unittest-тесты из командной строки для {name}?",
                     "python -m unittest",
                     ["python -m unittest.run", "python unittest.py", "unittest start"]),
                    ("Как проверить равенство результата {name} ожидаемому значению в unittest?",
                     "self.assertEqual(actual, expected)",
                     ["self.assert(actual == expected)", "assertEqual(self, actual, expected)", "self.equal(actual, expected)"]),
                ),
                Difficulty.MEDIUM: (
                    ("Как проверить, что вызов {name} вызывает исключение ValueError?",
                     "with self.assertRaises(ValueError):",
                     ["with self.raises(ValueError):", "self.assertRaises(ValueError, with {name})", "with unittest.raises(ValueError):"]),
                    ("Как выполнить общую подготовку перед каждым тестом {name}?",
                     "Определить setUp(self) в классе-наследнике unittest.TestCase",
                     ["Определить before_each(self) в TestCase", "Добавить setup() как статический метод", "Определить setUpClass() для выполнения перед каждым тестовым методом"]),
                    ("Как выполнить общую очистку после каждого теста {name}?",
                     "Определить tearDown(self) в классе-наследнике unittest.TestCase",
                     ["Определить after_each(self) в TestCase", "Определить tearDownClass() для очистки после каждого метода", "Вызвать self.cleanup() автоматически"]),
                    ("Как проверить, что результат {name} равен True?",
                     "self.assertTrue(result)",
                     ["self.assertEqualTrue(result)", "self.assertIsTrue(result)", "self.true(result)"]),
                    ("Как проверить, что результат {name} равен None?",
                     "self.assertIsNone(result)",
                     ["self.assertEqualNone(result)", "self.assertNone(result)", "self.is_none(result)"]),
                ),
                Difficulty.HARD: (
                    ("Как проверить несколько входных значений {name} одним тестовым методом без сторонних библиотек?",
                     "Перебрать кейсы в цикле и использовать with self.subTest(value=value):",
                     ["Применить @unittest.parametrize к методу TestCase", "Использовать self.parametrize(values)", "Стандартный unittest не поддерживает subTest"]),
                    ("Как сгруппировать несколько проверок для {name} с контекстом каждого случая в unittest?",
                     "Использовать with self.subTest(value=value): внутри цикла",
                     ["Использовать with self.testCase(value):", "Вызвать self.groupTest(value)", "Использовать @self.subTest над циклом"]),
                    ("Как временно заменить зависимость функции {name} в тесте стандартной библиотекой unittest?",
                     "Использовать unittest.mock.patch как декоратор или контекстный менеджер",
                     ["Использовать unittest.replace()", "Изменить исходный модуль через self.patch()", "Передать patch как аргумент в TestCase.run()"]),
                    ("Как проверить вызов mock-зависимости при выполнении {name}?",
                     "mock.assert_called_once_with(expected_arg)",
                     ["mock.assert_once(expected_arg)", "self.assertMockCalled(mock, expected_arg)", "mock.verify_call(expected_arg)"]),
                    ("Как запустить набор тестов {name} из Python-кода?",
                     "unittest.main()",
                     ["unittest.run_all()", "TestCase.main()", "unittest.start()"]),
                ),
                Difficulty.ADVANCED: (
                    ("Как гарантировать очистку ресурса, созданного для тестов {name}?",
                     "Зарегистрировать addCleanup() или использовать контекстный менеджер",
                     ["Очистить ресурс только в __del__ тестового класса", "Использовать setUpClass без очистки", "Перехватить BaseException и завершить процесс"]),
                    ("Как настроить общий ресурс на весь класс тестов {name}?",
                     "Использовать setUpClass(cls) и tearDownClass(cls)",
                     ["Использовать setUpModule(cls) и tearDownModule(cls)", "Определить setupClass(self) для каждого метода", "Создать ресурс в assertEqual()"]),
                    ("Как проверить, что исключение из {name} содержит ожидаемое сообщение?",
                     "Сохранить результат assertRaises в переменную и проверить exception.exception",
                     ["Проверить self.exception после блока", "Передать сообщение вторым аргументом assertRaises", "Сравнить traceback с результатом функции"]),
                    ("Как проверить, что mock вызван сначала с аргументом a, а затем b при {name}?",
                     "Проверить mock.call_args_list со списком unittest.mock.call",
                     ["Проверить только mock.call_args", "Вызвать mock.assert_has_calls([b, a])", "Сравнить mock.called с двумя аргументами"]),
                    ("Как определить отдельную последовательность тестов для {name} в unittest?",
                     "Создать unittest.TestSuite и добавить тесты методом addTest() или addTests()",
                     ["Создать unittest.TestGroup и вызвать append()", "Добавить методы в TestCase.tests", "Вызвать unittest.collect()"]),
                ),
                Difficulty.EXPERT: (
                    ("Как изолировать тесты {name}, которым нужны разные временные значения одного модуля?",
                     "Использовать patch.dict() или patch() в контекстном менеджере и гарантировать восстановление",
                     ["Менять глобальную переменную без восстановления", "Патчить зависимость после вызова тестируемой функции", "Использовать один общий mock для всех тестов без сброса"]),
                    ("Как тестировать асинхронную функцию {name} в современном unittest?",
                     "Наследовать тест от unittest.IsolatedAsyncioTestCase и объявить async def test_...",
                     ["Наследовать unittest.TestCase и вызвать asyncio.run() в каждом assert", "Использовать async def setUpClass без async тестов", "Унаследовать unittest.AsyncTestCase"]),
                    ("Как избежать утечки патча при ошибке внутри теста {name}?",
                     "Применять patch как контекстный менеджер или декоратор",
                     ["Вызывать patch.start() и не вызывать stop()", "Отключить восстановление моков", "Создать patch на уровне процесса без очистки"]),
                    ("Как повысить надежность проверок функции {name} с помощью unittest?",
                     "Использовать точные assertion-методы и проверять граничные случаи",
                     ["Заменить все проверки на assertTrue(True)", "Подавлять ошибки через try/except Exception", "Считать успешный импорт модуля тестом корректности"]),
                    ("Что следует проверять в тесте {name}, использующем mock внешнего API?",
                     "Контракт взаимодействия и результат поведения, не привязываясь к внутренним деталям реализации",
                     ["Число локальных переменных функции", "Точный порядок незначимых внутренних вызовов", "Только то, что mock был создан"]),
                ),
            }
        else:
            templates = {
                Difficulty.EASY: (
                    ("Как объявить простой тест для функции {name} в pytest?",
                     "def test_{name}():",
                     ["def unittest_{name}():", "class test_{name}:", "pytest def {name}():"]),
                    ("Как проверить равенство результата {name} ожидаемому значению в pytest?",
                     "assert actual == expected",
                     ["pytest.assertEqual(actual, expected)", "assertEqual(actual, expected)", "expect(actual).toEqual(expected)"]),
                    ("Как запустить тест проекта для функции {name} с помощью pytest?",
                     "pytest",
                     ["python -m pytest.run", "pytest.start()", "python -m unittest.pytest"]),
                    ("В каком файле pytest автоматически ищет тесты для {name} по умолчанию?",
                     "test_{name}.py",
                     ["{name}_spec.py", "pytest_{name}.py", "unittest_{name}.py"]),
                    ("Какой префикс обычно используют для имени тестовой функции pytest для {name}?",
                     "test_",
                     ["check_", "spec_", "pytest_"]),
                ),
                Difficulty.MEDIUM: (
                    ("Как проверить исключение ValueError при вызове {name} в pytest?",
                     "with pytest.raises(ValueError):",
                     ["with pytest.assertRaises(ValueError):", "pytest.raises(ValueError, with {name})", "with raises.pytest(ValueError):"]),
                    ("Как объявить фикстуру pytest для подготовки теста {name}?",
                     "@pytest.fixture",
                     ["@pytest.setup", "@pytest.parametrize", "@fixture.pytest"]),
                    ("Как передать фикстуру client в тестовую функцию pytest для {name}?",
                     "def test_{name}(client):",
                     ["def test_{name}(): client.fixture()", "def test_{name}(@pytest.fixture client):", "pytest.test(client, {name})"]),
                    ("Как параметризовать тест {name} несколькими наборами данных в pytest?",
                     "@pytest.mark.parametrize('value, expected', cases)",
                     ["@pytest.parametrize('value, expected', cases)", "@pytest.fixture.parametrize(cases)", "@pytest.mark.cases('value', cases)"]),
                    ("Как пропустить тест функции {name}, если условие стало известно во время выполнения?",
                     "При condition вызвать pytest.skip('reason') внутри теста",
                     ["pytest.ignore('reason')", "pytest.mark.skipif()", "pytest.stop('reason')"]),
                ),
                Difficulty.HARD: (
                    ("Как получить временный каталог для теста {name} в pytest?",
                     "Использовать встроенную фикстуру tmp_path",
                     ["Использовать pytest.tempdir()", "Использовать фикстуру temporary_path", "Вызвать tmp_path() как функцию модуля pytest"]),
                    ("Как выполнить очистку после yield в фикстуре pytest для {name}?",
                     "Разместить cleanup-код после yield в теле фикстуры",
                     ["Разместить cleanup перед yield", "Определить teardown_test() в тестовом модуле", "Вызвать pytest.cleanup() после теста вручную"]),
                    ("Как отметить тест {name} пользовательской pytest-меткой integration?",
                     "@pytest.mark.integration",
                     ["@pytest.integration", "@pytest.tag('integration')", "@pytest.mark('integration')"]),
                    ("Как проверить сообщение ожидаемого исключения в pytest при тестировании {name}?",
                     "Сохранить результат pytest.raises в переменную и проверить exc_info.value",
                     ["Проверить pytest.exception.message", "Передать expected_message в pytest.raises как позиционный аргумент", "Использовать assertRaisesMessage()"]),
                    ("Как выбрать тесты с меткой slow при запуске pytest для {name}?",
                     "pytest -m slow",
                     ["pytest --mark slow", "pytest --tag=slow", "pytest -k-marker slow"]),
                ),
                Difficulty.ADVANCED: (
                    ("Как ограничить время жизни фикстуры базы данных для тестов {name} одним тестовым модулем?",
                     "@pytest.fixture(scope='module')",
                     ["@pytest.fixture(lifetime='module')", "@pytest.mark.scope('module')", "@pytest.fixture(module=True)"]),
                    ("Как применять фикстуру autouse только в выбранном классе тестов {name}?",
                     "Объявить fixture с autouse=True в классе или подходящем conftest.py",
                     ["Передать autouse=True в параметр тестовой функции", "Добавить @pytest.autouse к классу", "pytest автоматически применит каждую фикстуру ко всем тестам"]),
                    ("Как переопределить фикстуру client только для набора тестов {name}?",
                     "Объявить фикстуру с тем же именем в тестовом модуле или классе",
                     ["Изменить pytest.fixture глобально во время запуска", "Передать новое значение в pytest.main()", "Создать фикстуру с произвольным именем без зависимости теста"]),
                    ("Как выполнить параметризованный тест {name} для всех элементов коллекции и дать кейсам имена?",
                     "@pytest.mark.parametrize('value', values, ids=case_ids)",
                     ["@pytest.fixture(params=values, ids=case_ids) над тестовой функцией", "@pytest.mark.parametrize(ids=case_ids)", "@pytest.parametrize('value', values, names=case_ids)"]),
                    ("Как использовать временную директорию pytest и автоматически удалить её после теста {name}?",
                     "Запросить встроенную фикстуру tmp_path как аргумент теста",
                     ["Создать tmp_path самостоятельно и вызвать pytest.remove()", "Использовать os.chdir('/') в конце теста", "Применить фикстуру tmp_path_factory без получения её результата"]),
                ),
                Difficulty.EXPERT: (
                    ("Как организовать общие фикстуры для тестов {name}, не делая их глобально доступными всему репозиторию?",
                     "Поместить фикстуры в conftest.py в нужной директории тестов",
                     ["Добавить фикстуры в site-packages/pytest.py", "Создать фикстуры в каждом тесте и monkeypatch pytest", "Объявить fixture в __init__.py корня приложения"]),
                    ("Как подменить атрибут модуля на время теста {name} встроенной фикстурой pytest?",
                     "Использовать monkeypatch.setattr()",
                     ["Использовать monkeypatch.patch()", "Вызвать pytest.mock_attribute()", "Изменить объект в pytest.config без восстановления"]),
                    ("Как проверять предупреждение, создаваемое функцией {name}, в pytest?",
                     "Использовать pytest.warns(ExpectedWarning) как контекстный менеджер",
                     ["Использовать pytest.raises(ExpectedWarning)", "Использовать pytest.warning(ExpectedWarning)", "Проверить sys.last_warning после теста"]),
                    ("Как проверить несколько ожидаемых результатов {name} и не потерять информацию о конкретном кейсе?",
                     "Параметризовать тест с pytest.mark.parametrize и задать ids для кейсов",
                     ["Объединить все проверки в один assert True", "Перехватить AssertionError и проигнорировать его", "Запустить цикл внутри фикстуры и вернуть только последний результат"]),
                    ("Как обеспечить изоляцию тестов {name}, которые используют изменяемое состояние?",
                     "Создавать состояние заново через фикстуру с подходящим scope и очищать его после yield",
                     ["Использовать одну session-scoped изменяемую фикстуру для всех тестов", "Порядком тестов заменить изоляцию", "Сохранять состояние в глобальной переменной между запусками"]),
                ),
            }
        return [
            (
                title.format(name=name),
                correct.format(name=name),
                [wrong.format(name=name) for wrong in wrong_answers],
            )
            for title, correct, wrong_answers in templates[difficulty]
            for name in names
        ]

    if subject == Subject.PANDAS:
        frames = (
            "sales", "students", "orders", "products", "employees",
            "customers", "weather", "flights", "books", "payments",
            "visits", "inventory", "tickets", "grades", "invoices",
            "sensors", "reviews", "shipments", "sessions", "events",
        )
        templates = {
            Difficulty.EASY: (
                ("Как создать DataFrame {frame} из словаря столбцов data?",
                 "{frame} = pd.DataFrame(data)",
                 ["{frame} = pd.Series(data)", "{frame} = pd.read(data)", "{frame} = DataFrame.from_dict(data)"]),
                ("Как прочитать CSV-файл в DataFrame {frame}?",
                 "{frame} = pd.read_csv('data.csv')",
                 ["{frame} = pd.read_csv('data')", "{frame} = pd.DataFrame.read_csv('data.csv')", "{frame} = pd.load_csv('data.csv')"]),
                ("Как получить число строк и столбцов таблицы {frame}?",
                 "{frame}.shape",
                 ["{frame}.size()", "{frame}.dimensions", "len({frame}.columns), len({frame}.rows)"]),
                ("Как выбрать столбец price из DataFrame {frame}?",
                 "{frame}['price']",
                 ["{frame}.price()", "{frame}.column('price')", "{frame}['columns']['price']"]),
                ("Как получить первые пять строк DataFrame {frame}?",
                 "{frame}.head()",
                 ["{frame}.first()", "{frame}.top(5)", "{frame}.iloc[:5, 1]"]),
            ),
            Difficulty.MEDIUM: (
                ("Как выбрать строку с индексной меткой 3 из таблицы {frame}?",
                 "{frame}.loc[3]",
                 ["{frame}.iloc[3]", "{frame}.row(3)", "{frame}.at_index(3)"]),
                ("Как выбрать строку с позиционным индексом 3 из таблицы {frame}?",
                 "{frame}.iloc[3]",
                 ["{frame}.loc[3]", "{frame}.row_at(3)", "{frame}.position(3)"]),
                ("Как отфильтровать строки {frame}, где столбец score больше 80?",
                 "{frame}[{frame}['score'] > 80]",
                 ["{frame}.filter('score' > 80)", "{frame}[{frame}['score'] = 80]", "{frame}.query(score > 80)"]),
                ("Как заменить пропуски в столбце value таблицы {frame} на 0?",
                 "{frame}['value'] = {frame}['value'].fillna(0)",
                 ["{frame}['value'].replace_na(0)", "{frame}.fill('value', 0)", "{frame}['value'].dropna(0)"]),
                ("Как отсортировать таблицу {frame} по столбцу date по возрастанию?",
                 "{frame}.sort_values('date')",
                 ["{frame}.sort('date')", "{frame}.order_by('date')", "{frame}.sort_index('date')"]),
            ),
            Difficulty.HARD: (
                ("Как вычислить среднее значение amount отдельно для каждой группы category в {frame}?",
                 "{frame}.groupby('category')['amount'].mean()",
                 ["{frame}.group('category').mean('amount')", "{frame}['amount'].groupby().mean('category')", "{frame}.mean(by='category', column='amount')"]),
                ("Как объединить таблицы {frame} и lookup по ключу id?",
                 "{frame}.merge(lookup, on='id')",
                 ["{frame}.concat(lookup, on='id')", "{frame}.join(lookup, key='id')", "{frame}.merge(lookup, index='id')"]),
                ("Как добавить строки таблицы extra в конец {frame}?",
                 "pd.concat([{frame}, extra], ignore_index=True)",
                 ["{frame}.merge(extra, axis=0)", "pd.concat({frame}, extra, axis='rows')", "{frame}.append_rows(extra, ignore_index=True)"]),
                ("Как оставить в таблице {frame} только уникальные строки по столбцу email?",
                 "{frame}.drop_duplicates(subset='email')",
                 ["{frame}.unique('email')", "{frame}.dropna(subset='email')", "{frame}.distinct('email')"]),
                ("Как построить сводную таблицу {frame} со средним amount по category и region?",
                 "{frame}.pivot_table(values='amount', index='category', columns='region', aggfunc='mean')",
                 ["{frame}.pivot(values='amount', index='category', columns='region', aggfunc='mean')", "{frame}.groupby('category', 'region').pivot('amount')", "{frame}.pivot_table('category', 'region', 'amount', mean=True)"]),
            ),
            Difficulty.ADVANCED: (
                ("Как агрегировать таблицу {frame}, получив count и mean столбца value по category?",
                 "{frame}.groupby('category').agg(count=('value', 'count'), mean=('value', 'mean'))",
                 ["{frame}.agg('category', count='value', mean='value')", "{frame}.groupby('category').apply(count='value', mean='value')", "{frame}.groupby('value').agg(count='category', mean='category')"]),
                ("Как добавить каждой строке {frame} среднее значение value для её группы category?",
                 "{frame}['group_mean'] = {frame}.groupby('category')['value'].transform('mean')",
                 ["{frame}['group_mean'] = {frame}.groupby('category')['value'].agg('mean')", "{frame}['group_mean'] = {frame}.transform('category', 'value')", "{frame}['group_mean'] = {frame}.groupby('value')['category'].transform('mean')"]),
                ("Как превратить столбцы Jan и Feb таблицы {frame} в строки с помощью melt?",
                 "{frame}.melt(id_vars='id', value_vars=['Jan', 'Feb'])",
                 ["{frame}.pivot(id_vars='id', value_vars=['Jan', 'Feb'])", "{frame}.stack(columns=['Jan', 'Feb'])", "{frame}.melt(index='id', columns=['Jan', 'Feb'])"]),
                ("Как преобразовать строковый столбец date таблицы {frame} в datetime?",
                 "{frame}['date'] = pd.to_datetime({frame}['date'])",
                 ["{frame}['date'] = pd.datetime({frame}['date'])", "{frame}.to_datetime('date')", "{frame}['date'] = {frame}['date'].astype('datetime')"]),
                ("Как получить накопительную сумму amount внутри каждой группы account таблицы {frame}?",
                 "{frame}.groupby('account')['amount'].cumsum()",
                 ["{frame}.cumsum('amount', by='account')", "{frame}.groupby('amount')['account'].sum()", "{frame}.groupby('account')['amount'].cumulate()"]),
            ),
            Difficulty.EXPERT: (
                ("Как проверить взаимно-однозначное соответствие ключа при объединении {frame} с lookup?",
                 "{frame}.merge(lookup, on='id', validate='one_to_one')",
                 ["{frame}.merge(lookup, on='id', validate='many_to_many')", "{frame}.merge(lookup, on='id', unique=True)", "{frame}.join(lookup, on='id', validate='one_to_one')"]),
                ("Как читать большой CSV для обработки {frame} частями по 10000 строк?",
                 "for chunk in pd.read_csv('data.csv', chunksize=10000): ...",
                 ["for chunk in pd.read_csv('data.csv', batch=10000): ...", "pd.read_csv('data.csv').chunks(10000)", "pd.read_csv('data.csv', iterator=10000)"]),
                ("Как безопасно изменить status для строк таблицы {frame}, где score меньше 50?",
                 "{frame}.loc[{frame}['score'] < 50, 'status'] = 'review'",
                 ["{frame}[{frame}['score'] < 50]['status'] = 'review'", "{frame}.iloc[{frame}['score'] < 50, 'status'] = 'review'", "{frame}.set('status', 'review', where='score' < 50)"]),
                ("Как оптимизировать хранение повторяющихся строковых значений в столбце category таблицы {frame}?",
                 "{frame}['category'] = {frame}['category'].astype('category')",
                 ["{frame}['category'] = {frame}['category'].astype('object')", "{frame}.category.encode()", "{frame}['category'] = pd.to_numeric({frame}['category'])"]),
                ("Как выполнить временную агрегацию amount по дням для таблицы {frame} с datetime-индексом?",
                 "{frame}.resample('D')['amount'].sum()",
                 ["{frame}.groupby('D')['amount'].resample()", "{frame}.resample('day', column='amount').aggregate()", "{frame}.rolling('D')['amount'].groupby().sum()"]),
            ),
        }
        return [
            (
                title.format(frame=frame),
                correct.format(frame=frame),
                [wrong.format(frame=frame) for wrong in wrong_answers],
            )
            for title, correct, wrong_answers in templates[difficulty]
            for frame in frames
        ]

    if subject == Subject.REGEX:
        regex_contexts = {
            Difficulty.EASY: (
                [f"order-{index}-item" for index in range(1, 21)],
                [f"api{index}.example.com" for index in range(1, 21)],
                [f"item{index}tag" for index in range(1, 21)],
                [f"Python{index} guide" for index in range(1, 21)],
                [f"word{index} next" for index in range(1, 21)],
            ),
            Difficulty.MEDIUM: (
                [f"year={2000 + index}" for index in range(1, 21)],
                [f"({200 + index:03}) 555-{1000 + index}" for index in range(1, 21)],
                [f"{'cat' if index % 2 else 'dog'} animal{index}" for index in range(1, 21)],
                [f"key={index}; count={index + 10}" for index in range(1, 21)],
                [f"status={index}; result=ok" for index in range(1, 21)],
            ),
            Difficulty.HARD: (
                [f"<tag{index}>value</tag{index}>" for index in range(1, 21)],
                [f"item #{index} ready" for index in range(1, 21)],
                [f"word{index} word{index}" for index in range(1, 21)],
                [f"one{index}, two{index}, three{index}" for index in range(1, 21)],
                [f"{'PYTHON' if index % 2 else 'Python'} version {index}" for index in range(1, 21)],
            ),
            Difficulty.ADVANCED: (
                [f"{2000 + index}-{(index % 12) + 1:02}-15" for index in range(1, 21)],
                [f"{index}A item{index}" for index in range(1, 21)],
                [f"person{index}@example.com" for index in range(1, 21)],
                [f"values {index} and {index + 1}" for index in range(1, 21)],
                [f"{'PYTHON' if index % 2 else 'Python'} guide {index}" for index in range(1, 21)],
            ),
            Difficulty.EXPERT: (
                [f"person{index}@example.com" for index in range(1, 21)],
                [f"Python line {index}\nother line" for index in range(1, 21)],
                [f"<tag{index}>value</tag{index}>" for index in range(1, 21)],
                [f'first "{index}" and second "{index + 1}"' for index in range(1, 21)],
                [f"host 192.168.{index}.1 and 10.0.0.1" for index in range(1, 21)],
            ),
        }
        regex_templates = {
            Difficulty.EASY: (
                ("Какой шаблон найдёт одну или несколько цифр в строке «{sample}» через re.search?",
                 r"r'\d+'", [r"r'\D+'", r"r'\w+'", r"r'\d?'"]),
                ("Как найти именно точку «.» в строке «{sample}» через регулярное выражение?",
                 r"r'\.'", [r"r'.'", r"r'\\.'", r"r'\d'"]),
                ("Какой шаблон найдёт последовательность латинских букв в «{sample}»?",
                 r"r'[A-Za-z]+'", [r"r'[0-9]+'", r"r'\s+'", r"r'[^A-Za-z]+'"]),
                ("Как проверить, что строка «{sample}» начинается с Python, используя re.match?",
                 r"r'^Python'", [r"r'Python$'", r"r'\APython$'", r"r'\\^Python'"]),
                ("Какой шаблон найдёт один или несколько пробельных символов в «{sample}»?",
                 r"r'\s+'", [r"r'\S+'", r"r'\w+'", r"r'\s?'"]),
            ),
            Difficulty.MEDIUM: (
                ("Как найти отдельную последовательность ровно из четырёх цифр в строке «{sample}»?",
                 r"r'\b\d{4}\b'", [r"r'\b\d{3}\b'", r"r'\d{4,}'", r"r'\D{4}'"]),
                ("Как извлечь три цифры телефонного кода в скобках из строки «{sample}»?",
                 r"r'\((\d{3})\)'", [r"r'\[\d{3}\]'", r"r'\((\w{3})\)'", r"r'(\d{3})\)'"]),
                ("Как найти отдельное слово ровно из трёх латинских букв в «{sample}»?",
                 r"r'\b[A-Za-z]{3}\b'", [r"r'\b[A-Za-z]{2}\b'", r"r'\b\d{3}\b'", r"r'[A-Za-z]{3,}'"]),
                ("Как найти отдельное слово cat или dog в строке «{sample}»?",
                 r"r'\b(?:cat|dog)\b'", [r"r'\bcatdog\b'", r"r'cat.*dog'", r"r'\b(?:bird|fish)\b'"]),
                ("Как заменить каждую последовательность цифр на # в строке «{sample}» через re.sub?",
                 r"re.sub(r'\d+', '#', text)", [r"re.sub(r'\D+', '#', text)", r"re.sub(r'\d', text, '#')", r"text.replace(r'\d+', '#')"]),
            ),
            Difficulty.HARD: (
                ("Как найти кратчайшие фрагменты между угловыми скобками в строке «{sample}»?",
                 r"re.findall(r'<.*?>', text)", [r"re.findall(r'<.*>', text)", r"re.findall(r'\\<.*?\\>', text)", r"re.findall(r'<.+>', text)"]),
                ("Как найти слово сразу после символа # в строке «{sample}» с помощью lookbehind?",
                 r"r'(?<=#)\w+'", [r"r'(?<!#)\w+'", r"r'#(?<=\w+)'", r"r'(?=#)\w+'"]),
                ("Как найти повторяющееся подряд слово в строке «{sample}»?",
                 r"r'\b(\w+)\s+\1\b'", [r"r'\b(\w+)\s+\2\b'", r"r'\b(\w+)\s+\w+\b'", r"r'\b\1\s+(\w+)\b'"]),
                ("Как разделить строку «{sample}» по запятым, игнорируя пробелы вокруг них?",
                 r"re.split(r'\s*,\s*', text)", [r"re.split(r'\s+\s*', text)", r"re.split(r',\s+', text)", r"re.split(r'\s*,', text)"]),
                ("Как скомпилировать шаблон python для поиска без учета регистра в строке «{sample}»?",
                 r"re.compile(r'python', re.IGNORECASE)", [r"re.compile(r'python', re.ASCII)", r"re.compile(r'Python', re.MULTILINE)", r"re.compile(r'python', re.VERBOSE)"]),
            ),
            Difficulty.ADVANCED: (
                ("Как извлечь год, месяц и день из строки «{sample}» в именованные группы?",
                 r"r'(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})'",
                 [r"r'(?<year>\d{4})-(?<month>\d{2})-(?<day>\d{2})'", r"r'(?P=year)-(?P=month)-(?P=day)'", r"r'(?P<year>\d{2})-(?P<month>\d{2})-(?P<day>\d{4})'"]),
                ("Как найти число в строке «{sample}», только если после него сразу стоит буква?",
                 r"r'\d+(?=[A-Za-z])'", [r"r'\d+(?![A-Za-z])'", r"r'(?<=[A-Za-z])\d+'", r"r'\d+[A-Za-z]'"]),
                ("Как извлечь домен из email «{sample}» через именованную группу domain?",
                 r"r'^[^@\s]+@(?P<domain>[^@\s]+)$'",
                 [r"r'^(?P<domain>[^@\s]+)@[^@\s]+$'", r"r'^[^@]+(?P=domain)@[^@]+$'", r"r'@(?P<domain>\s+)'"]),
                ("Как заменить каждую группу цифр её квадратом в строке «{sample}»?",
                 r"re.sub(r'\d+', lambda m: str(int(m.group()) ** 2), text)",
                 [r"re.sub(r'\d+', str(int(m.group()) ** 2), text)", r"re.sub(r'\D+', lambda m: str(int(m.group()) ** 2), text)", r"re.sub(r'\d+', lambda m: m.group() ** 2, text)"]),
                ("Как найти все слова Python в строке «{sample}» без учета регистра и с Unicode-границами слов?",
                 r"re.findall(r'\bpython\b', text, flags=re.IGNORECASE)",
                 [r"re.findall(r'python', text, flags=re.ASCII)", r"re.match(r'\bpython\b', text, flags=re.IGNORECASE)", r"re.findall(r'^python$', text, flags=re.IGNORECASE)"]),
            ),
            Difficulty.EXPERT: (
                ("Как проверить, что вся строка «{sample}» целиком соответствует шаблону email?",
                 r"re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', text)",
                 [r"re.search(r'[^@\s]+@[^@\s]+\.[^@\s]+', text)", r"re.match(r'@[^@\s]+\.[^@\s]+', text)", r"re.fullmatch(r'[^@]+@[^@]+', text)"]),
                ("Как найти в многострочном тексте «{sample}» строки, начинающиеся с Python?",
                 r"re.findall(r'^Python.*$', text, flags=re.MULTILINE)",
                 [r"re.findall(r'^Python.*$', text)", r"re.findall(r'\APython.*\Z', text, flags=re.MULTILINE)", r"re.findall(r'Python.*', text, flags=re.DOTALL)"]),
                ("Как сопоставить открывающий и такой же закрывающий тег в строке «{sample}» обратной ссылкой?",
                 r"r'<([A-Za-z][A-Za-z0-9]*)>.*?</\1>'",
                 [r"r'<([A-Za-z][A-Za-z0-9]*)>.*?</\2>'", r"r'<\1>.*?</([A-Za-z]+)>'", r"r'<([0-9]+)>.*?</\1>'"]),
                ("Как извлечь значения между кавычками в строке «{sample}», не включая кавычки в результат?",
                 r"""re.findall(r'"([^"]*)"', text)""",
                 [r"""re.findall(r'"(.*)"', text)""", r"""re.findall(r'[^"]+', text)""", r"""re.findall(r'"([^"]+)', text)"""]),
                ("Как найти IPv4-кандидаты в строке «{sample}», а затем проверить диапазоны октетов?",
                 r"Сначала найти кандидаты регулярным выражением, затем проверить каждый октет в диапазоне 0–255",
                 ["Считать любое совпадение r'\\d{1,3}(\\.\\d{1,3}){3}' гарантированно корректным IPv4", "Проверить только количество точек", "Использовать re.match(r'.*', text) без проверки результата"]),
            ),
        }
        return [
            (
                title.replace("{sample}", sample),
                correct,
                wrong,
            )
            for (title, correct, wrong), examples in zip(
                regex_templates[difficulty],
                regex_contexts[difficulty],
            )
            for sample in examples
        ]

    targets = (
        "numbers", "records", "messages", "prices", "users",
        "products", "events", "results", "values", "items",
        "tokens", "orders", "points", "names", "files",
        "samples", "chunks", "entries", "tasks", "responses",
    )
    templates = {
        (Subject.GENERATOR, Difficulty.EASY): (
            ("Как объявить генератор, который поэлементно выдает данные {target}?",
             "def generate_{target}({target}): yield from {target}",
             ["def generate_{target}({target}): return yield {target}", "generator generate_{target}({target}): {target}", "def generate_{target}({target}): yield from list"]),
            ("Как получить итератор-генератор из элементов {target}?",
             "({target}_item for {target}_item in {target})",
             ["[{target}_item for {target}_item in {target}]", "{target}.generator({target}_item)", "yield ({target}_item in {target})"]),
            ("Как получить следующий элемент из итератора {target}_iterator?",
             "next({target}_iterator)",
             ["{target}_iterator.next()", "next_iterator({target}_iterator)", "{target}_iterator.get_next()"]),
            ("Как перебрать все элементы генератора {target}_iterator?",
             "for item in {target}_iterator:",
             ["for item in next({target}_iterator):", "while {target}_iterator.has_next():", "{target}_iterator.foreach(item)"]),
            ("Какое исключение возникает при вызове next() у исчерпанного генератора {target}?",
             "StopIteration",
             ["IndexError", "GeneratorExit", "EOFError"]),
        ),
        (Subject.GENERATOR, Difficulty.MEDIUM): (
            ("Как передать элементы другого итерируемого объекта {target} из генератора?",
             "yield from {target}",
             ["return from {target}", "yield *{target}", "await {target}"]),
            ("Как собрать все значения генератора {target}_generator в список?",
             "list({target}_generator)",
             ["tuple({target}_generator).list()", "{target}_generator.to_list()", "list.next({target}_generator)"]),
            ("Как безопасно получить следующий элемент {target}_iterator или значение по умолчанию?",
             "next({target}_iterator, default)",
             ["{target}_iterator.next(default=default)", "next(default, {target}_iterator)", "getattr({target}_iterator, default)"]),
            ("Что произойдет при повторном обходе уже исчерпанного генератора {target}?",
             "Он не выдаст элементы повторно; для нового обхода нужно создать генератор заново",
             ["Он автоматически перемотается к первому элементу", "Он повторит последний элемент", "Он преобразуется в список автоматически"]),
            ("Когда выполняется тело генераторной функции {target} с yield?",
             "При запросе следующего значения, а не при создании объекта генератора",
             ["Сразу целиком при вызове функции", "Только при сборке мусора", "При импорте модуля независимо от вызова"]),
        ),
        (Subject.GENERATOR, Difficulty.HARD): (
            ("Что делает вызов {target}_generator.send(value) после первого next()?",
             "Передает value в приостановленный генератор как результат выражения yield",
             ["Заменяет объект генератора новым списком", "Передает аргумент в исходную функцию генератора повторно", "Пропускает все оставшиеся yield"]),
            ("Какой метод корректно запрашивает завершение генератора {target}_generator?",
             "{target}_generator.close()",
             ["{target}_generator.stop()", "{target}_generator.cancel()", "{target}_generator.join()"]),
            ("Как узнать состояние генератора {target}_generator средствами inspect?",
             "inspect.getgeneratorstate({target}_generator)",
             ["inspect.generator_state({target}_generator)", "inspect.state({target}_generator)", "{target}_generator.get_state()"]),
            ("Почему генератор {target} обычно экономит память по сравнению со списком?",
             "Он вычисляет и хранит значения по мере запроса, а не весь результат сразу",
             ["Он автоматически сжимает каждый объект", "Он всегда выполняется в отдельном процессе", "Он хранит все значения в кэше CPU"]),
            ("Что должно быть выполнено перед первым send(non_none) в {target}_generator?",
             "Сначала продвинуть генератор вызовом next() или send(None)",
             ["Сначала вызвать close()", "Сначала преобразовать его в tuple", "Никакой подготовки не требуется"]),
        ),
        (Subject.GENERATOR, Difficulty.ADVANCED): (
            ("Как делегировать подгенератору {target}_iterator передачу значений и завершение?",
             "yield from {target}_iterator",
             ["return yield {target}_iterator", "await from {target}_iterator", "yield*({target}_iterator)"]),
            ("Где получить значение, переданное в StopIteration при return из генератора {target}?",
             "В атрибуте исключения StopIteration.value",
             ["В атрибуте StopIteration.result", "В последнем значении next()", "В sys.generator_return"]),
            ("Как корректно обрабатывать закрытие генератора {target} при освобождении ресурса?",
             "Использовать try/finally вокруг yield и освобождать ресурс в finally",
             ["Освобождать ресурс только после последнего next()", "Перехватывать BaseException и подавлять его", "Вызывать return до входа в try"]),
            ("Что делает выражение (item for item in {target})?",
             "Создает ленивый объект генератора",
             ["Немедленно создает список элементов", "Создает функцию с именем item", "Запускает отдельный поток для обхода"]),
            ("Как сохранить ленивую обработку большого объекта {target} при конвейерной обработке?",
             "Соединять генераторы через yield from или генераторные выражения без материализации списка",
             ["Преобразовать каждый этап в list", "Сначала скопировать все элементы в tuple", "Вызвать sorted() на каждом промежуточном этапе"]),
        ),
        (Subject.GENERATOR, Difficulty.EXPERT): (
            ("Как гарантировать освобождение ресурса генератора {target} при досрочном прекращении обхода?",
             "Обеспечить закрытие генератора, например через contextlib.closing, и cleanup в finally",
             ["Полагаться на немедленный запуск сборщика мусора", "Вызывать next() бесконечно до конца", "Перехватить StopIteration снаружи и не закрывать генератор"]),
            ("Что важно при передаче исключения в приостановленный генератор {target}_generator через throw()?",
             "Исключение возбуждается в точке приостановки на yield и может быть обработано внутри генератора",
             ["Исключение всегда подавляется без входа в генератор", "throw() перезапускает функцию с начала", "Исключение передается в вызывающий код как обычное значение"]),
            ("Почему нельзя повторно использовать один генератор {target}_iterator для нескольких независимых потребителей?",
             "Потребители совместно продвигают один курсор и забирают значения друг у друга",
             ["Генератор автоматически клонирует состояние для каждого цикла", "Итераторы Python нельзя передавать функциям", "Каждый next() создает новый объект генератора"]),
            ("Как безопасно обрабатывать GeneratorExit при очистке генератора {target}?",
             "Выполнить cleanup и не пытаться выдать новое значение после получения GeneratorExit",
             ["Перехватить GeneratorExit и бесконечно yield-ить", "Заменить его на StopIteration вручную", "Игнорировать cleanup, поскольку close() его не вызывает"]),
            ("Как проверить ленивый конвейер генераторов {target} на ограниченное потребление данных?",
             "Запрашивать только нужное число элементов через itertools.islice и проверять побочные эффекты итерации",
             ["Преобразовать конвейер целиком в список", "Вызывать len() у генератора", "Повторно обходить тот же генератор, ожидая те же элементы"]),
        ),
        (Subject.DECORATOR, Difficulty.EASY): (
            ("Как применить декоратор @trace к функции {target}?",
             "@trace\ndef {target}(): ...",
             ["trace@\ndef {target}(): ...", "def {target}() @trace: ...", "decorate({target}) def(): ..."]),
            ("Что должен вернуть простой декоратор после определения wrapper для {target}?",
             "wrapper",
             ["Результат вызова функции сразу", "None всегда", "Исходные аргументы функции"]),
            ("Как вызвать исходную функцию {target} внутри wrapper без аргументов?",
             "return {target}()",
             ["return call({target})", "yield {target}", "return wrapper({target})"]),
            ("Когда применяется синтаксис @decorator над функцией {target}?",
             "При создании функции; это эквивалентно {target} = decorator({target})",
             ["При каждом импорте вызова функции", "Только после первого вызова функции", "После завершения программы"]),
            ("Какую задачу обычно решает декоратор функции {target}?",
             "Оборачивает функцию дополнительным поведением, сохраняя возможность ее вызвать",
             ["Автоматически превращает функцию в класс", "Гарантированно запускает функцию в отдельном процессе", "Заменяет все аргументы функции типами"]),
        ),
        (Subject.DECORATOR, Difficulty.MEDIUM): (
            ("Как написать wrapper, принимающий произвольные позиционные и именованные аргументы {target}?",
             "def wrapper(*args, **kwargs): return {target}(*args, **kwargs)",
             ["def wrapper(args, kwargs): return {target}(args, kwargs)", "def wrapper(**args, *kwargs): return {target}()", "def wrapper(...): return {target}"]),
            ("Как сохранить имя и docstring функции {target} при декорировании?",
             "@functools.wraps({target}) над wrapper",
             ["@functools.keep({target}) над wrapper", "@functools.signature({target}) над wrapper", "wrapper.__name__ = functools.name({target})"]),
            ("Как вернуть из wrapper результат вызова декорируемой функции {target}?",
             "return {target}(*args, **kwargs)",
             ["{target}(*args, **kwargs); return None", "yield {target}(*args, **kwargs)", "return args, kwargs"]),
            ("Как передать параметр timeout в декоратор для функции {target}?",
             "Создать декоратор-фабрику timeout(value), возвращающую декоратор функции",
             ["Добавить параметр в functools.wraps({target})", "Передать timeout в __name__ функции", "Использовать yield timeout над функцией"]),
            ("Какое значение обычно хранит замыкание декоратора вокруг {target}?",
             "Ссылку на декорируемую функцию и параметры декоратора",
             ["Только локальные переменные вызывающего потока", "Автоматическую копию результата каждого вызова", "Список всех модулей Python"]),
        ),
        (Subject.DECORATOR, Difficulty.HARD): (
            ("В каком порядке применятся @outer и @inner над функцией {target}?",
             "{target} = outer(inner({target}))",
             ["{target} = inner(outer({target}))", "{target} = outer({target}) и inner не вызывается", "{target} = inner({target}) и outer не вызывается"]),
            ("Как написать декоратор с параметром retries для функции {target}?",
             "def retry(retries): return decorator; decorator возвращает wrapper для {target}",
             ["def retry(retries): return {target}()", "Добавить retries в functools.wraps", "Использовать @retry[{target}]"]),
            ("Как декоратору {target} прозрачно повторно выбросить необработанное исключение?",
             "После нужной обработки использовать raise внутри except",
             ["Использовать return None в except", "Вызвать sys.exit() для любого исключения", "Создать новое исключение без исходной причины"]),
            ("Как сохранить передачу результата исходной функции {target} через wrapper?",
             "Вернуть результат вызова {target} из wrapper",
             ["Вернуть сам wrapper", "Не возвращать значение из wrapper", "Всегда возвращать True"]),
            ("Как корректно объединить @classmethod с другим декоратором для {target}?",
             "Учитывать порядок применения и обычно ставить @classmethod непосредственно над def, под внешним декоратором",
             ["Декораторы всегда коммутативны", "Ставить @classmethod после тела метода", "Заменить @classmethod на functools.wraps"]),
        ),
        (Subject.DECORATOR, Difficulty.ADVANCED): (
            ("Как построить параметризованный декоратор, сохраняющий метаданные {target}?",
             "Фабрика параметров возвращает декоратор, который применяет functools.wraps к wrapper",
             ["Фабрика сразу вызывает {target} при импорте", "Хранить параметры в {target}.__doc__", "Использовать wraps вместо wrapper и декоратора"]),
            ("Что нужно сделать wrapper декоратора, чтобы обернуть генераторную функцию {target} и сохранить ленивость?",
             "Самому быть генератором и делегировать значения через yield from",
             ["Материализовать результат в list при декорировании", "Вернуть объект generator без вызова функции", "Вызвать next() только один раз и вернуть значение"]),
            ("Как реализовать декоратор класса для {target}, не меняя класс без необходимости?",
             "Принять класс и вернуть тот же или явно модифицированный класс",
             ["Вернуть результат вызова конструктора класса", "Обязательно заменить класс строкой", "Добавить wrapper только к __name__ и вернуть None"]),
            ("Почему functools.wraps важен для introspection декорированной функции {target}?",
             "Он копирует метаданные и устанавливает __wrapped__ на исходную функцию",
             ["Он кэширует результат функции", "Он делает функцию потокобезопасной", "Он автоматически сохраняет все вызовы в базе"]),
            ("Как композиционно применяются несколько декораторов к {target}?",
             "Снизу вверх: ближайший к def декоратор применяется первым",
             ["Сверху вниз независимо от синтаксиса", "Все декораторы вызываются только при вызове функции", "Порядок определяется алфавитом имен декораторов"]),
        ),
        (Subject.DECORATOR, Difficulty.EXPERT): (
            ("Как корректно декорировать async-функцию {target}, сохранив ее асинхронный контракт?",
             "Использовать async wrapper и await исходную функцию внутри него",
             ["Вызвать asyncio.run({target}()) из wrapper", "Использовать обычный wrapper и не await результат", "Преобразовать coroutine в list"]),
            ("Как обработать отмену async-функции внутри декоратора {target}?",
             "Выполнить cleanup в finally и не подавлять CancelledError без явной причины",
             ["Перехватить BaseException и вернуть None", "Повторять отмененную операцию бесконечно", "Вызвать loop.stop() из wrapper"]),
            ("Как сделать кэш-декоратор для функции {target} корректным при изменяемом общем состоянии?",
             "Определить ключ кэша, политику инвалидирования и синхронизацию общего кэша",
             ["Кэшировать по id изменяемого аргумента навсегда", "Не проверять аргументы и возвращать первый результат", "Считать functools.wraps механизмом синхронизации"]),
            ("Как декоратору {target} безопасно сохранять исходную сигнатуру для introspection?",
             "Применить functools.wraps и при необходимости настроить __signature__ согласно контракту",
             ["Заменить __code__ без учета аргументов", "Скопировать только __name__", "Сохранить сигнатуру в глобальной переменной без привязки к функции"]),
            ("Как избежать утечки ссылок при декораторе-кэше долгоживущей функции {target}?",
             "Ограничить размер или срок жизни кэша и определить явную политику очистки",
             ["Хранить все аргументы и результаты бесконечно", "Отключить сборщик мусора", "Копировать кэш в новый wrapper при каждом вызове"]),
        ),
    }
    selected_templates = templates.get((subject, difficulty), ())
    if not selected_templates:
        return []
    return [
        (
            title.format(target=target),
            correct.format(target=target),
            [wrong.format(target=target) for wrong in wrong_answers],
        )
        for title, correct, wrong_answers in selected_templates
        for target in targets
    ]


def question_title_without_generated_number(title: str) -> str:
    _, generated_marker, generated_title = title.partition(" · задание ")
    if not generated_marker:
        return title
    _, separator, generated_title = generated_title.partition(": ")
    return generated_title if separator else title


def seed_questions(db: Session) -> None:
    for question in db.scalars(select(Question)).all():
        question.title = question_title_without_generated_number(question.title)

    if db.query(Question).count() == 0:
        db.add_all([
            Question(
                title="Как объявить функцию в Python?",
                description="Выберите один правильный вариант.",
                question_type=QuestionType.SINGLE,
                difficulty=Difficulty.EASY,
                points=1,
                reference_answer="def greet():",
                options=[
                    Option(label="function greet():", is_correct=False, position=1),
                    Option(label="def greet():", is_correct=True, position=2),
                    Option(label="func greet():", is_correct=False, position=3),
                    Option(label="define greet():", is_correct=False, position=4),
                ],
            ),
            Question(
                title="Какие из перечисленных являются изменяемыми типами?",
                description="Выберите все подходящие варианты.",
                question_type=QuestionType.MULTIPLE,
                difficulty=Difficulty.MEDIUM,
                points=2,
                reference_answer="list, dict",
                options=[
                    Option(label="list", is_correct=True, position=1),
                    Option(label="tuple", is_correct=False, position=2),
                    Option(label="dict", is_correct=True, position=3),
                    Option(label="str", is_correct=False, position=4),
                ],
            ),
            Question(
                title="Напишите функцию, которая возвращает сумму списка чисел",
                description="Можно ответить объяснением, кодом или загрузить готовый .py-файл.",
                question_type=QuestionType.WRITTEN,
                difficulty=Difficulty.HARD,
                points=5,
                starter_code="def list_sum(numbers):\n    # Напишите решение\n    pass",
                reference_answer="def list_sum(numbers):\n    return sum(numbers)",
            ),
        ])
        db.flush()

    generated_by_difficulty = {difficulty: [] for difficulty in PYTHON_DIFFICULTIES}
    for first in range(20):
        for second in range(1, 21):
            easy_answer = first + second
            easy_wrong = list(dict.fromkeys(
                value
                for value in (easy_answer + 1, easy_answer - 1, first * second)
                if value != easy_answer
            ))
            candidate = easy_answer + 2
            while len(easy_wrong) < 3:
                if candidate != easy_answer and candidate not in easy_wrong:
                    easy_wrong.append(candidate)
                candidate += 1
            generated_by_difficulty[Difficulty.EASY].append((
                f"Какое значение получится у выражения {first} + {second}?",
                str(easy_answer),
                [str(value) for value in easy_wrong],
            ))

            values = [first + second * index for index in range(5)]
            list_index = (first + second) % len(values)
            medium_answer = values[list_index]
            medium_wrong = [medium_answer + 1, medium_answer - 1, medium_answer + 2]
            generated_by_difficulty[Difficulty.MEDIUM].append((
                f"Что вернёт выражение numbers[{list_index}] для numbers = {values}?",
                str(medium_answer),
                [str(value) for value in medium_wrong],
            ))

            outer_count = first + 1
            inner_count = second
            hard_answer = sum(
                outer * inner
                for outer in range(1, outer_count + 1)
                for inner in range(1, inner_count + 1)
            )
            hard_wrong = list(dict.fromkeys(
                hard_answer + offset
                for offset in (1, -1, outer_count, -inner_count, 2, -2)
            ))[:3]
            generated_by_difficulty[Difficulty.HARD].append((
                f"Чему будет равен total, если перед циклами задать total = 0, а внутри вложенных циклов for i in range(1, {outer_count + 1}) и for j in range(1, {inner_count + 1}) выполнять total += i * j?",
                str(hard_answer),
                [str(value) for value in hard_wrong],
            ))

            advanced_start = first
            advanced_stop = first + second
            advanced_divisor = first % 5 + 2
            advanced_remainder = first % advanced_divisor
            advanced_answer = sum(
                1
                for value in range(advanced_start, advanced_stop)
                if value % advanced_divisor == advanced_remainder
            )
            generated_by_difficulty[Difficulty.ADVANCED].append((
                f"Сколько элементов создаст выражение [x ** 2 for x in range({advanced_start}, {advanced_stop}) if x % {advanced_divisor} == {advanced_remainder}]?",
                str(advanced_answer),
                [str(advanced_answer + 1), str(advanced_answer - 1), str(advanced_answer + 2)],
            ))

            recursion_depth = first + 1
            initial_value = second - 1
            expert_answer = initial_value
            for _ in range(recursion_depth):
                expert_answer = 2 * expert_answer + 1
            expert_wrong = list(dict.fromkeys(
                expert_answer + offset
                for offset in (1, -1, recursion_depth, recursion_depth + 1, 2, -2)
            ))[:3]
            generated_by_difficulty[Difficulty.EXPERT].append((
                f"Что вернет рекурсивная функция f(n), если f(0) = {initial_value}, f(n) = 2 * f(n - 1) + 1, и вызвать ее с n = {recursion_depth}?",
                str(expert_answer),
                [str(value) for value in expert_wrong],
            ))

    for difficulty, candidates in generated_by_difficulty.items():
        existing = unique_questions_for_difficulty(db, difficulty)
        seen = {question_content_key(question) for question in existing}
        missing_count = max(0, 100 - len(existing))
        if missing_count == 0:
            continue
        new_questions = []
        for title, correct, wrong in candidates:
            question = Question(
                title=title,
                description="Выберите один правильный вариант.",
                question_type=QuestionType.SINGLE,
                subject=Subject.PYTHON,
                difficulty=difficulty,
                points=1,
                reference_answer=correct,
                options=[
                    Option(label=value, is_correct=value == correct, position=position)
                    for position, value in enumerate([correct, *wrong], 1)
                ],
            )
            content_key = question_content_key(question)
            if content_key in seen:
                continue
            seen.add(content_key)
            new_questions.append(question)
            if len(new_questions) == missing_count:
                break
        if len(new_questions) < missing_count:
            raise RuntimeError(f"Не удалось создать 100 уникальных вопросов уровня {difficulty.value}")
        db.add_all(new_questions)
        db.flush()

    for difficulty in FASTAPI_DIFFICULTIES:
        existing = unique_questions_for_difficulty(db, difficulty, Subject.FASTAPI)
        seen = {question_content_key(question) for question in existing}
        missing_count = max(0, 100 - len(existing))
        if missing_count == 0:
            continue
        new_questions = []
        for title, correct, wrong in fastapi_question_candidates(difficulty):
            question = Question(
                title=title,
                description="Выберите один правильный вариант.",
                question_type=QuestionType.SINGLE,
                subject=Subject.FASTAPI,
                difficulty=difficulty,
                points=1,
                reference_answer=correct,
                options=[
                    Option(label=value, is_correct=value == correct, position=position)
                    for position, value in enumerate([correct, *wrong], 1)
                ],
            )
            content_key = question_content_key(question)
            if content_key in seen:
                continue
            seen.add(content_key)
            new_questions.append(question)
            if len(new_questions) == missing_count:
                break
        if len(new_questions) < missing_count:
            raise RuntimeError(f"Не удалось создать 100 уникальных вопросов FastAPI уровня {difficulty.value}")
        db.add_all(new_questions)
        db.flush()

    for difficulty in PYTHON_DIFFICULTIES:
        candidates = aiogram_question_candidates(difficulty)
        existing = unique_questions_for_difficulty(db, difficulty, Subject.AIOGRAM)
        seen = {question_content_key(question) for question in existing}
        missing_count = max(0, 100 - len(existing))
        new_questions = []
        for title, correct, wrong in candidates:
            question = Question(
                title=title,
                description="Выберите один правильный вариант. Вопросы составлены для aiogram 3.x.",
                question_type=QuestionType.SINGLE,
                subject=Subject.AIOGRAM,
                difficulty=difficulty,
                points=1,
                reference_answer=correct,
                options=[
                    Option(label=value, is_correct=value == correct, position=position)
                    for position, value in enumerate([correct, *wrong], 1)
                ],
            )
            content_key = question_content_key(question)
            if content_key in seen:
                continue
            seen.add(content_key)
            new_questions.append(question)
            if len(new_questions) == missing_count:
                break
        if len(new_questions) < missing_count:
            raise RuntimeError(f"Не удалось создать 100 уникальных вопросов aiogram уровня {difficulty.value}")
        db.add_all(new_questions)
        db.flush()

    for subject in CONCURRENCY_SUBJECTS:
        for difficulty in PYTHON_DIFFICULTIES:
            candidates = concurrency_question_candidates(subject, difficulty)
            existing = unique_questions_for_difficulty(db, difficulty, subject)
            seen = {question_content_key(question) for question in existing}
            missing_count = max(0, 100 - len(existing))
            new_questions = []
            for title, correct, wrong in candidates:
                question = Question(
                    title=title,
                    description="Выберите один правильный вариант.",
                    question_type=QuestionType.SINGLE,
                    subject=subject,
                    difficulty=difficulty,
                    points=1,
                    reference_answer=correct,
                    options=[
                        Option(label=value, is_correct=value == correct, position=position)
                        for position, value in enumerate([correct, *wrong], 1)
                    ],
                )
                content_key = question_content_key(question)
                if content_key in seen:
                    continue
                seen.add(content_key)
                new_questions.append(question)
                if len(new_questions) == missing_count:
                    break
            if len(new_questions) < missing_count:
                raise RuntimeError(
                    f"Не удалось создать 100 уникальных вопросов {subject.value} уровня {difficulty.value}"
                )
            db.add_all(new_questions)
            db.flush()

    for subject in (*PYTHON_BASES_SUBJECTS, *PYTHON_BASICS_TOPIC_SUBJECTS):
        for difficulty in PYTHON_DIFFICULTIES:
            candidates = python_concept_question_candidates(subject, difficulty)
            if subject in PYTHON_BASICS_TOPIC_SUBJECTS:
                if len(candidates) < 100:
                    raise RuntimeError(
                        f"Недостаточно уникальных вопросов для темы {subject.value}"
                    )
                existing = db.scalars(
                    select(Question)
                    .where(
                        Question.difficulty == difficulty,
                        Question.subject == subject,
                        Question.is_active.is_(True),
                    )
                    .options(selectinload(Question.options))
                    .order_by(Question.id)
                ).all()
                expected_questions = [
                    Question(
                        title=title,
                        description="Выберите один правильный вариант.",
                        question_type=QuestionType.SINGLE,
                        subject=subject,
                        difficulty=difficulty,
                        points=1,
                        reference_answer=correct,
                        options=[
                            Option(label=value, is_correct=value == correct, position=position)
                            for position, value in enumerate([correct, *wrong], 1)
                        ],
                    )
                    for title, correct, wrong in candidates[:100]
                ]
                expected_keys = {question_content_key(question) for question in expected_questions}
                current_keys = {question_content_key(question) for question in existing}
                if len(existing) != 100 or current_keys != expected_keys:
                    for question in existing:
                        question.is_active = False
                    db.add_all(expected_questions)
                    db.flush()
                continue

            existing = unique_questions_for_difficulty(db, difficulty, subject)
            seen = {question_content_key(question) for question in existing}
            missing_count = max(0, 100 - len(existing))
            new_questions = []
            for title, correct, wrong in candidates:
                question = Question(
                    title=title,
                    description="Выберите один правильный вариант.",
                    question_type=QuestionType.SINGLE,
                    subject=subject,
                    difficulty=difficulty,
                    points=1,
                    reference_answer=correct,
                    options=[
                        Option(label=value, is_correct=value == correct, position=position)
                        for position, value in enumerate([correct, *wrong], 1)
                    ],
                )
                content_key = question_content_key(question)
                if content_key in seen:
                    continue
                seen.add(content_key)
                new_questions.append(question)
                if len(new_questions) == missing_count:
                    break
            if len(new_questions) < missing_count:
                raise RuntimeError(
                    f"Не удалось создать 100 уникальных вопросов {subject.value} уровня {difficulty.value}"
                )
            db.add_all(new_questions)
            db.flush()

    for subject in PYTHON_CONCEPT_SUBJECTS:
        for difficulty in PYTHON_DIFFICULTIES:
            candidates = python_concept_question_candidates(subject, difficulty)
            existing = unique_questions_for_difficulty(db, difficulty, subject)
            seen = {question_content_key(question) for question in existing}
            missing_count = max(0, 100 - len(existing))
            new_questions = []
            for title, correct, wrong in candidates:
                question = Question(
                    title=title,
                    description="Выберите один правильный вариант.",
                    question_type=QuestionType.SINGLE,
                    subject=subject,
                    difficulty=difficulty,
                    points=1,
                    reference_answer=correct,
                    options=[
                        Option(label=value, is_correct=value == correct, position=position)
                        for position, value in enumerate([correct, *wrong], 1)
                    ],
                )
                content_key = question_content_key(question)
                if content_key in seen:
                    continue
                seen.add(content_key)
                new_questions.append(question)
                if len(new_questions) == missing_count:
                    break
            if len(new_questions) < missing_count:
                raise RuntimeError(
                    f"Не удалось создать 100 уникальных вопросов {subject.value} уровня {difficulty.value}"
                )
            db.add_all(new_questions)
            db.flush()

    for question in db.scalars(select(Question).where(Question.reference_answer.is_(None))).all():
        question.reference_answer = "Смотрите правильные варианты выше."
    for question in db.scalars(select(Question).where(Question.difficulty.is_(None))).all():
        question.difficulty = list(Difficulty)[(question.id - 1) % len(Difficulty)]
    db.commit()


def ensure_schema() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("questions")}
    if "reference_answer" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE questions ADD COLUMN reference_answer TEXT"))
    if "difficulty" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE questions ADD COLUMN difficulty VARCHAR(20)"))
            connection.execute(text("UPDATE questions SET difficulty = CASE ((id - 1) % 3) WHEN 0 THEN 'easy' WHEN 1 THEN 'medium' ELSE 'hard' END WHERE difficulty IS NULL"))
    if "subject" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE questions ADD COLUMN subject VARCHAR(20) NOT NULL DEFAULT 'python'"))
    if "is_active" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE questions ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))
    submission_columns = {column["name"] for column in inspect(engine).get_columns("submissions")}
    if "user_id" not in submission_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE submissions ADD COLUMN user_id INTEGER REFERENCES users(id)"))
    for column, sql_type, default in (
        ("grade", "INTEGER", "2"),
        ("subject", "VARCHAR(30)", "'python'"),
        ("difficulty", "VARCHAR(20)", "'medium'"),
        ("written_pending", "BOOLEAN", "FALSE"),
    ):
        if column not in submission_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE submissions ADD COLUMN {column} {sql_type} NOT NULL DEFAULT {default}")
                )


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    with SessionLocal() as db:
        seed_questions(db)
    clear_difficulty_cache()
    yield


app = FastAPI(title="Python Test Lab", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def serialize_question(question: Question) -> dict:
    return {
        "id": question.id,
        "title": question.title,
        "description": question.description,
        "question_type": question.question_type,
        "difficulty": question.difficulty,
        "points": question.points,
        "starter_code": question.starter_code,
        "options": [
            {"id": option.id, "label": option.label}
            for option in question.options
        ],
    }


def question_content_key(question: Question) -> tuple:
    return (
        getattr(question.question_type, "value", question.question_type),
        question_title_without_generated_number(question.title).strip().casefold(),
        (question.reference_answer or "").strip().casefold(),
        tuple(sorted((option.label.strip().casefold(), option.is_correct) for option in question.options)),
    )


def unique_questions_for_difficulty(
    db: Session,
    difficulty: Difficulty,
    subject: Subject = Subject.PYTHON,
) -> list[Question]:
    questions = db.scalars(
        select(Question)
        .where(
            Question.difficulty == difficulty,
            Question.subject == subject,
            Question.is_active.is_(True),
        )
        .options(selectinload(Question.options))
        .order_by(Question.id)
    ).all()
    unique_questions = {}
    for question in questions:
        unique_questions.setdefault(question_content_key(question), question)
    return list(unique_questions.values())


def clear_difficulty_cache() -> None:
    with _difficulty_cache_lock:
        _difficulty_cache.clear()


def difficulty_results_for_subjects(
    db: Session,
    subjects: list[Subject],
) -> dict[str, list[dict[str, str | int]]]:
    requested_subjects = list(dict.fromkeys(subjects))
    cache_key = tuple(sorted(subject.value for subject in requested_subjects))
    now = time.monotonic()
    with _difficulty_cache_lock:
        cached = _difficulty_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        difficulties_by_subject = {
            subject: available_difficulties(subject) for subject in requested_subjects
        }
        counts = {
            subject: {difficulty: 0 for difficulty in difficulties}
            for subject, difficulties in difficulties_by_subject.items()
        }
        seen_questions: dict[tuple[Subject, Difficulty], set[tuple]] = {}
        questions = db.scalars(
            select(Question)
            .where(
                Question.subject.in_([subject.value for subject in requested_subjects]),
                Question.is_active.is_(True),
            )
            .options(joinedload(Question.options))
        ).unique().all()
        for question in questions:
            subject = Subject(question.subject)
            difficulty = Difficulty(question.difficulty)
            if difficulty not in counts[subject]:
                continue
            group = (subject, difficulty)
            content_key = question_content_key(question)
            if content_key in seen_questions.setdefault(group, set()):
                continue
            seen_questions[group].add(content_key)
            counts[subject][difficulty] += 1

        results = {
            subject.value: [
                {
                    "id": difficulty.value,
                    "label": DIFFICULTY_LABELS[difficulty],
                    "question_count": counts[subject][difficulty],
                }
                for difficulty in difficulties
            ]
            for subject, difficulties in difficulties_by_subject.items()
        }
        expired_keys = [
            key for key, (expires_at, _) in _difficulty_cache.items() if expires_at <= now
        ]
        for key in expired_keys:
            del _difficulty_cache[key]
        if len(_difficulty_cache) >= 32:
            _difficulty_cache.pop(next(iter(_difficulty_cache)))
        _difficulty_cache[cache_key] = (now + DIFFICULTY_CACHE_TTL_SECONDS, results)
        return results


def available_difficulties(subject: Subject) -> tuple[Difficulty, ...]:
    return FASTAPI_DIFFICULTIES if subject == Subject.FASTAPI else PYTHON_DIFFICULTIES


@app.get("/", response_class=FileResponse)
def index() -> Path:
    return BASE_DIR / "static" / "index.html"


@app.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/api/auth/register")
def register(payload: AccountCredentials, response: Response, db: Session = Depends(get_db)) -> dict:
    username = payload.username.strip()
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Введите корректный email")
    user = User(username=username, email=email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        account = create_session(response, user, db)
    except IntegrityError:
        db.rollback()
        if db.scalar(select(User).where(User.username == username)):
            raise HTTPException(status_code=409, detail="Это имя пользователя уже занято")
        if db.scalar(select(User).where(User.email == email)):
            raise HTTPException(status_code=409, detail="Этот email уже зарегистрирован")
        raise
    return account


@app.post("/api/auth/login")
def login(payload: LoginCredentials, response: Response, db: Session = Depends(get_db)) -> dict:
    login_value = payload.login.strip()
    user = db.scalar(
        select(User).where((User.username == login_value) | (User.email == login_value.lower()))
    )
    if user is None:
        username_matches = db.scalars(
            select(User)
            .where(func.lower(User.username) == login_value.lower())
            .limit(2)
        ).all()
        if len(username_matches) == 1:
            user = username_matches[0]
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    return create_session(response, user, db)


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "username": user.username, "email": user.email}


@app.get("/api/statistics")
def get_statistics(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    submissions = db.scalars(
        select(Submission)
        .where(Submission.user_id == user.id)
        .order_by(Submission.created_at.desc(), Submission.id.desc())
    ).all()
    completed_article_ids = db.scalars(
        select(ArticleProgress.article_id).where(ArticleProgress.user_id == user.id)
    ).all()
    attempts = [
        {
            "id": submission.id,
            "subject": submission.subject,
            "subject_label": SUBJECT_LABELS.get(Subject(submission.subject), submission.subject),
            "difficulty": submission.difficulty,
            "difficulty_label": (
                "Тематический тест"
                if Subject(submission.subject) in PYTHON_BASICS_TOPIC_SUBJECTS
                else DIFFICULTY_LABELS.get(
                    Difficulty(submission.difficulty), submission.difficulty
                )
            ),
            "score": submission.score,
            "max_score": submission.max_score,
            "score_percent": round(submission.score / submission.max_score * 100, 1)
            if submission.max_score
            else 0,
            "grade": submission.grade,
            "grade_label": GRADE_LABELS[submission.grade],
            "written_pending": submission.written_pending,
            "created_at": submission.created_at.isoformat(),
        }
        for submission in submissions[:30]
    ]
    score_percentages = [
        submission.score / submission.max_score * 100
        for submission in submissions
        if submission.max_score
    ]
    return {
        "completed_article_ids": completed_article_ids,
        "completed_material_count": len(completed_article_ids),
        "total_material_count": len(ARTICLE_IDS),
        "completed_test_count": len(submissions),
        "average_score_percent": round(sum(score_percentages) / len(score_percentages), 1)
        if score_percentages
        else 0,
        "attempts": attempts,
    }


@app.post("/api/statistics/materials")
def mark_material_completed(
    payload: ArticleProgressUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if payload.article_id not in ARTICLE_IDS:
        raise HTTPException(status_code=404, detail="Материал не найден")
    progress = db.scalar(
        select(ArticleProgress).where(
            ArticleProgress.user_id == user.id,
            ArticleProgress.article_id == payload.article_id,
        )
    )
    if progress is None:
        db.add(ArticleProgress(user_id=user.id, article_id=payload.article_id))
        db.commit()
    return {"article_id": payload.article_id, "completed": True}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> dict:
    if session_token:
        session = db.scalar(select(UserSession).where(UserSession.token == session_token))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie("session_token")
    return {"ok": True}


@app.get("/api/difficulties")
def get_difficulties(
    subject: Subject = Query(Subject.PYTHON),
    db: Session = Depends(get_db),
) -> dict:
    return {"difficulties": difficulty_results_for_subjects(db, [subject])[subject.value]}


@app.get("/api/difficulties/batch")
def get_difficulties_batch(
    subjects: list[Subject] = Query(),
    db: Session = Depends(get_db),
) -> dict:
    return {
        "difficulties": difficulty_results_for_subjects(db, subjects)
    }


@app.get("/api/subjects")
def get_subjects() -> dict:
    return {
        "subjects": [
            {"id": Subject.PYTHON.value, "label": SUBJECT_LABELS[Subject.PYTHON], "has_difficulties": True},
            {"id": Subject.FASTAPI.value, "label": SUBJECT_LABELS[Subject.FASTAPI], "has_difficulties": False},
            {"id": Subject.AIOGRAM.value, "label": SUBJECT_LABELS[Subject.AIOGRAM], "has_difficulties": True},
            *[
                {"id": subject.value, "label": SUBJECT_LABELS[subject], "has_difficulties": True}
                for subject in (*CONCURRENCY_SUBJECTS, *PYTHON_CONCEPT_SUBJECTS)
            ],
            *[
                {"id": subject.value, "label": SUBJECT_LABELS[subject], "has_difficulties": False}
                for subject in PYTHON_BASICS_TOPIC_SUBJECTS
            ],
        ]
    }


@app.get("/api/test")
def get_test(
    difficulty: Difficulty = Query(Difficulty.MEDIUM),
    subject: Subject = Query(Subject.PYTHON),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if difficulty not in available_difficulties(subject):
        raise HTTPException(status_code=400, detail="Этот уровень недоступен для выбранной темы")
    questions = unique_questions_for_difficulty(db, difficulty, subject)
    if len(questions) < 20:
        raise HTTPException(status_code=400, detail="Для этого уровня пока меньше 20 заданий")
    if subject in PYTHON_BASICS_TOPIC_SUBJECTS and len(questions) >= 100:
        questions = [random.choice(questions[index::20]) for index in range(20)]
        random.shuffle(questions)
    else:
        questions = random.sample(questions, k=min(20, len(questions)))
    return {
        "title": {
            Subject.PYTHON: "Python foundations",
            Subject.PYTHON_BASES: "Основы Python",
            Subject.FASTAPI: "Тест по FastAPI",
            Subject.AIOGRAM: "Создание ботов на aiogram",
            **{subject: SUBJECT_LABELS[subject] for subject in CONCURRENCY_SUBJECTS},
            **{subject: SUBJECT_LABELS[subject] for subject in PYTHON_BASES_SUBJECTS},
            **{subject: SUBJECT_LABELS[subject] for subject in PYTHON_BASICS_TOPIC_SUBJECTS},
            **{subject: SUBJECT_LABELS[subject] for subject in PYTHON_CONCEPT_SUBJECTS},
        }[subject],
        "subtitle": (
            f"Тест по теме «{SUBJECT_LABELS[subject]}»"
            if subject in PYTHON_BASICS_TOPIC_SUBJECTS
            else f"Уровень: {DIFFICULTY_LABELS[difficulty]}. Проверьте знания {SUBJECT_LABELS[subject]} и объясните ход решения"
        ),
        "difficulty": difficulty.value,
        "difficulty_label": DIFFICULTY_LABELS[difficulty],
        "is_topic_test": subject in PYTHON_BASICS_TOPIC_SUBJECTS,
        "subject": subject.value,
        "subject_label": SUBJECT_LABELS[subject],
        "question_count": len(questions),
        "questions": [serialize_question(question) for question in questions],
    }


@app.post("/api/submit")
def submit_test(
    payload: TestSubmission,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    question_ids = {answer.question_id for answer in payload.answers}
    if len(question_ids) != 20:
        raise HTTPException(status_code=400, detail="В каждой попытке должно быть 20 заданий")
    questions = db.scalars(
        select(Question)
        .where(
            Question.difficulty == payload.difficulty,
            Question.subject == payload.subject,
            Question.is_active.is_(True),
        )
        .options(selectinload(Question.options))
        .order_by(Question.id)
    ).all()
    questions = [question for question in questions if question.id in question_ids]
    if len(questions) != 20:
        raise HTTPException(status_code=400, detail="Некоторые задания не найдены")
    questions_by_id = {question.id: question for question in questions}
    answers_by_id = {answer.question_id: answer for answer in payload.answers}

    if set(answers_by_id) - set(questions_by_id):
        raise HTTPException(status_code=400, detail="В ответах есть неизвестный вопрос")

    score = 0
    max_score = sum(question.points for question in questions)
    result_items = []
    submission = Submission(
        user_id=user.id,
        score=0,
        max_score=max_score,
        subject=payload.subject.value,
        difficulty=payload.difficulty.value,
        written_pending=False,
    )
    db.add(submission)
    db.flush()

    for question in questions:
        submitted = answers_by_id.get(question.id, SubmittedAnswer(question_id=question.id))
        selected_ids = sorted(set(submitted.selected_option_ids))
        correct_ids = sorted(option.id for option in question.options if option.is_correct)
        is_correct = False
        earned_points = 0

        if question.question_type in (QuestionType.SINGLE, QuestionType.MULTIPLE):
            valid_ids = {option.id for option in question.options}
            if set(selected_ids) - valid_ids:
                raise HTTPException(status_code=400, detail="Недопустимый вариант ответа")
            is_correct = selected_ids == correct_ids
            earned_points = question.points if is_correct else 0
        else:
            is_correct = bool((submitted.answer_text or "").strip())
            earned_points = 0

        score += earned_points
        db.add(
            Answer(
                submission_id=submission.id,
                question_id=question.id,
                answer_text=(submitted.answer_text or "").strip() or None,
                selected_option_ids=",".join(map(str, selected_ids)) or None,
                is_correct=is_correct,
                earned_points=earned_points,
            )
        )
        result_items.append(
            {
                "question_id": question.id,
                "is_correct": is_correct,
                "earned_points": earned_points,
                "max_points": question.points,
                "is_written": question.question_type == QuestionType.WRITTEN,
                "question_title": question.title,
                "correct_answers": [option.label for option in question.options if option.is_correct],
                "reference_answer": question.reference_answer,
                "your_answer": submitted.answer_text or ", ".join(
                    option.label for option in question.options if option.id in selected_ids
                ) or "Нет ответа",
            }
        )

    submission.score = score
    submission.grade = grade_for_score(score, max_score)
    submission.written_pending = any(item["is_written"] for item in result_items)
    db.commit()
    correct_count = sum(item["is_correct"] for item in result_items)
    question_count = len(result_items)
    incorrect_count = question_count - correct_count
    return {
        "submission_id": submission.id,
        "score": score,
        "max_score": max_score,
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "correct_percent": round(correct_count / question_count * 100, 1),
        "incorrect_percent": round(incorrect_count / question_count * 100, 1),
        "score_percent": round(score / max_score * 100, 1) if max_score else 0,
        "grade": submission.grade,
        "grade_label": GRADE_LABELS[submission.grade],
        "written_pending": submission.written_pending,
        "results": result_items,
    }


@app.post("/api/submit-file")
async def submit_file(
    question_id: int = Form(...),
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".py"):
        raise HTTPException(status_code=400, detail="Загрузите файл с расширением .py")
    content = await file.read()
    if len(content) > 100_000:
        raise HTTPException(status_code=400, detail="Файл не должен быть больше 100 КБ")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="Файл должен быть в кодировке UTF-8") from error
    return {"question_id": question_id, "filename": file.filename, "content": decoded}
print("Python Test Lab API is running...")
