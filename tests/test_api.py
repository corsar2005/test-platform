from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base, get_db
from models import Option, Question


@pytest.fixture
def test_session_factory() -> Iterator[sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield test_session
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def test_client(test_session_factory: sessionmaker) -> Iterator[TestClient]:
    main.clear_difficulty_cache()

    def override_get_db() -> Iterator[Session]:
        with test_session_factory() as db:
            yield db

    main.app.dependency_overrides[get_db] = override_get_db
    client = TestClient(main.app)
    try:
        yield client
    finally:
        client.close()
        main.app.dependency_overrides.pop(get_db, None)
        main.clear_difficulty_cache()


def register_user(client: TestClient, username: str = "learner") -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.test",
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 200
    return response.json()


def create_test_questions(
    session_factory: sessionmaker,
    *,
    count: int = 20,
    subject: str = "python",
    difficulty: str = "easy",
    written_last: bool = False,
) -> list[dict[str, int | None]]:
    questions: list[dict[str, int | None]] = []
    with session_factory() as db:
        for index in range(count):
            is_written = written_last and index == count - 1
            question = Question(
                title=f"Тестовый вопрос {index}",
                description="Выберите правильный ответ.",
                question_type="written" if is_written else "single",
                subject=subject,
                difficulty=difficulty,
                points=1,
                reference_answer="Тестовый ответ",
            )
            if not is_written:
                question.options = [
                    Option(label="Правильно", is_correct=True, position=1),
                    Option(label="Неправильно", is_correct=False, position=2),
                ]
            db.add(question)
            db.flush()
            questions.append(
                {
                    "question_id": question.id,
                    "correct_option_id": question.options[0].id if question.options else None,
                    "wrong_option_id": question.options[1].id if question.options else None,
                }
            )
        db.commit()
    return questions


def submission_payload(
    questions: list[dict[str, int | None]],
    *,
    correct_count: int = 20,
    subject: str = "python",
    difficulty: str = "easy",
) -> dict:
    answers = []
    for index, question in enumerate(questions):
        answer = {"question_id": question["question_id"], "selected_option_ids": []}
        if question["correct_option_id"] is not None:
            option_key = "correct_option_id" if index < correct_count else "wrong_option_id"
            answer["selected_option_ids"] = [question[option_key]]
        answers.append(answer)
    return {"answers": answers, "subject": subject, "difficulty": difficulty}


@pytest.mark.parametrize(
    ("score", "max_score", "expected_grade"),
    [(0, 20, 2), (9, 20, 2), (10, 20, 3), (14, 20, 4), (15, 20, 4), (17, 20, 5), (0, 0, 2)],
)
def test_grade_for_score_boundaries(score: int, max_score: int, expected_grade: int) -> None:
    assert main.grade_for_score(score, max_score) == expected_grade


def test_password_hash_is_salted_and_verified() -> None:
    first_hash = main.hash_password("correct-horse-battery")
    second_hash = main.hash_password("correct-horse-battery")

    assert first_hash != second_hash
    assert main.verify_password("correct-horse-battery", first_hash)
    assert not main.verify_password("wrong-password", first_hash)
    assert not main.verify_password("password", "not-a-valid-hash")


def test_protected_endpoints_require_login(test_client: TestClient) -> None:
    response = test_client.get("/api/statistics")

    assert response.status_code == 401


def test_register_login_and_logout(test_client: TestClient) -> None:
    registration = test_client.post(
        "/api/auth/register",
        json={
            "username": "learner",
            "email": "learner@example.test",
            "password": "correct-horse-battery",
        },
    )
    account = registration.json()
    registration_cookie = test_client.cookies.get("session_token")

    assert registration.status_code == 200
    assert account["username"] == "learner"
    assert registration_cookie
    assert "httponly" in registration.headers["set-cookie"].lower()

    me_response = test_client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "learner@example.test"

    test_client.post("/api/auth/logout")
    assert test_client.get("/api/auth/me").status_code == 401

    login_response = test_client.post(
        "/api/auth/login",
        json={"login": "LEARNER@example.test", "password": "correct-horse-battery"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["id"] == account["id"]


@pytest.mark.parametrize(
    ("username", "email", "expected_status"),
    [
        ("learner", "another@example.test", 409),
        ("another", "learner@example.test", 409),
    ],
)
def test_register_rejects_duplicate_accounts(
    test_client: TestClient,
    username: str,
    email: str,
    expected_status: int,
) -> None:
    register_user(test_client)

    response = test_client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": "correct-horse-battery"},
    )

    assert response.status_code == expected_status


@pytest.mark.parametrize("username", ["   ", "  ab  "])
def test_register_rejects_username_shorter_than_three_after_trimming(
    test_client: TestClient,
    username: str,
) -> None:
    response = test_client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": "learner@example.test",
            "password": "correct-horse-battery",
        },
    )

    assert response.status_code == 422
    assert test_client.cookies.get("session_token") is None


def test_register_trims_username_and_normalizes_email(test_client: TestClient) -> None:
    response = test_client.post(
        "/api/auth/register",
        json={
            "username": "  learner  ",
            "email": " LEARNER@EXAMPLE.TEST ",
            "password": "correct-horse-battery",
        },
    )

    assert response.status_code == 200
    assert response.json()["username"] == "learner"
    assert response.json()["email"] == "learner@example.test"
    assert test_client.post(
        "/api/auth/login",
        json={"login": "  learner  ", "password": "correct-horse-battery"},
    ).status_code == 200


def test_login_matches_username_without_case_sensitivity(test_client: TestClient) -> None:
    account = test_client.post(
        "/api/auth/register",
        json={
            "username": "CaseSensitive",
            "email": "case@example.test",
            "password": "correct-horse-battery",
        },
    ).json()
    test_client.post("/api/auth/logout")

    response = test_client.post(
        "/api/auth/login",
        json={"login": "casesensitive", "password": "correct-horse-battery"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == account["id"]


def test_register_uses_one_database_write_transaction(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    statements: list[str] = []
    bind = test_session_factory.kw["bind"]

    def capture_statement(*args: object) -> None:
        statements.append(str(args[2]).lstrip().split(maxsplit=1)[0].upper())

    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        response = test_client.post(
            "/api/auth/register",
            json={
                "username": "fast-registration",
                "email": "fast@example.test",
                "password": "correct-horse-battery",
            },
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert statements.count("SELECT") == 0
    assert statements.count("INSERT") == 2


def test_login_rejects_incorrect_password(test_client: TestClient) -> None:
    register_user(test_client)

    response = test_client.post(
        "/api/auth/login",
        json={"login": "learner", "password": "incorrect-password"},
    )

    assert response.status_code == 401


def test_subject_list_contains_theory_topics(test_client: TestClient) -> None:
    response = test_client.get("/api/subjects")

    assert response.status_code == 200
    subject_ids = {subject["id"] for subject in response.json()["subjects"]}
    assert {"python", "fastapi", "py_conditions", "databases"} <= subject_ids


def test_difficulty_counts_available_questions(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    create_test_questions(test_session_factory, count=20)

    response = test_client.get("/api/difficulties", params={"subject": "python"})

    assert response.status_code == 200
    difficulties = {item["id"]: item for item in response.json()["difficulties"]}
    assert difficulties["easy"]["question_count"] == 20


def test_test_endpoint_returns_twenty_questions(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    register_user(test_client)
    create_test_questions(test_session_factory, count=25)

    response = test_client.get(
        "/api/test",
        params={"subject": "python", "difficulty": "easy"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["question_count"] == 20
    assert len({question["id"] for question in result["questions"]}) == 20
    assert len({question["title"].strip().casefold() for question in result["questions"]}) == 20


def test_python_question_bank_covers_multiple_skills_at_each_difficulty() -> None:
    family_prefixes = {
        main.Difficulty.EASY: (
            "Вычислите сумму",
            "Чему равно произведение",
            "Сколько символов",
            "Что вернет [",
            "Истинно ли выражение",
        ),
        main.Difficulty.MEDIUM: (
            "Какой список вернет values",
            "Что вернет profile.get",
            "Каким станет список",
            "Сколько итераций",
            "Что вернет выражение",
        ),
        main.Difficulty.HARD: (
            "Чему равна сумма i * j",
            "Какие числа останутся",
            "Что вернет calculate",
            "Какой словарь создаст",
            "Какие пары создаст zip",
        ),
        main.Difficulty.ADVANCED: (
            "Что создаст [x ** 2",
            "Чему равно sum(x * 2",
            "Что вернет list(map",
            "В каком порядке окажутся",
            "Какой список даст",
        ),
        main.Difficulty.EXPERT: (
            "Что вернет f(",
            "Какой список вернет вызов add",
            "Чему равны результаты двух последовательных",
            "Что вернут функции из",
            "Чему равен результат reduce",
        ),
    }

    candidates = main.python_question_candidates()
    for difficulty, prefixes in family_prefixes.items():
        questions = candidates[difficulty][:100]
        assert len(questions) == 100
        assert len({title.casefold() for title, _, _ in questions}) == 100
        assert {prefix for prefix in prefixes if any(
            title.startswith(prefix) for title, _, _ in questions
        )} == set(prefixes)
        assert all(
            len(set([correct, *wrong])) == 4
            for _, correct, wrong in questions
        )


def test_questions_with_same_title_are_deduplicated(
    test_session_factory: sessionmaker,
) -> None:
    questions = create_test_questions(test_session_factory, count=2)
    with test_session_factory() as db:
        duplicate = db.get(Question, questions[1]["question_id"])
        assert duplicate is not None
        duplicate.title = "  ТЕСТОВЫЙ   ВОПРОС 0  "
        duplicate.reference_answer = "Другой ответ"
        db.commit()

        assert len(main.unique_questions_for_difficulty(db, main.Difficulty.EASY)) == 1

        main.deactivate_duplicate_questions(db)
        db.commit()

        active_questions = db.query(Question).filter(Question.is_active.is_(True)).all()
        assert len(active_questions) == 1


def test_obsolete_python_question_templates_are_deactivated(
    test_session_factory: sessionmaker,
) -> None:
    questions = create_test_questions(test_session_factory, count=2)
    with test_session_factory() as db:
        obsolete = db.get(Question, questions[0]["question_id"])
        current = db.get(Question, questions[1]["question_id"])
        assert obsolete is not None
        assert current is not None
        obsolete.title = "Какое значение получится у выражения 3 + 4?"

        main.deactivate_obsolete_python_questions(db)
        db.commit()
        db.refresh(obsolete)
        db.refresh(current)

        assert obsolete.is_active is False
        assert current.is_active is True


def test_test_endpoint_rejects_unavailable_difficulty(test_client: TestClient) -> None:
    register_user(test_client)

    response = test_client.get(
        "/api/test",
        params={"subject": "python", "difficulty": "master"},
    )

    assert response.status_code == 400


def test_submit_saves_grade_answers_and_statistics(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    register_user(test_client)
    questions = create_test_questions(test_session_factory)

    response = test_client.post(
        "/api/submit",
        json=submission_payload(questions, correct_count=17),
    )

    assert response.status_code == 200
    result = response.json()
    assert (result["score"], result["max_score"], result["grade"]) == (17, 20, 5)
    assert (result["correct_count"], result["incorrect_count"]) == (17, 3)

    statistics = test_client.get("/api/statistics").json()
    assert statistics["completed_test_count"] == 1
    assert statistics["attempts"][0]["subject"] == "python"
    assert statistics["attempts"][0]["difficulty"] == "easy"
    assert statistics["attempts"][0]["grade"] == 5
    assert statistics["attempts"][0]["score_percent"] == 85.0


def test_submit_marks_written_answers_as_pending(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    register_user(test_client)
    questions = create_test_questions(test_session_factory, written_last=True)
    payload = submission_payload(questions, correct_count=19)
    payload["answers"][-1]["answer_text"] = "Моё решение"

    response = test_client.post("/api/submit", json=payload)

    assert response.status_code == 200
    assert response.json()["written_pending"] is True
    statistics = test_client.get("/api/statistics").json()
    assert statistics["attempts"][0]["written_pending"] is True


def test_submit_requires_twenty_distinct_question_ids(test_client: TestClient) -> None:
    register_user(test_client)

    response = test_client.post(
        "/api/submit",
        json={
            "subject": "python",
            "difficulty": "easy",
            "answers": [{"question_id": index} for index in range(19)],
        },
    )

    assert response.status_code == 400


def test_submit_rejects_option_from_another_question(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    register_user(test_client)
    questions = create_test_questions(test_session_factory)
    payload = submission_payload(questions)
    payload["answers"][0]["selected_option_ids"] = [questions[1]["correct_option_id"]]

    response = test_client.post("/api/submit", json=payload)

    assert response.status_code == 400


def test_material_progress_is_idempotent_and_user_scoped(test_client: TestClient) -> None:
    register_user(test_client)
    first_user_cookie = test_client.cookies.get("session_token")
    payload = {"article_id": "python"}

    first_mark = test_client.post("/api/statistics/materials", json=payload)
    second_mark = test_client.post("/api/statistics/materials", json=payload)
    first_statistics = test_client.get("/api/statistics").json()

    assert first_mark.status_code == 200
    assert second_mark.status_code == 200
    assert first_statistics["completed_material_count"] == 1
    assert first_statistics["completed_article_ids"] == ["python"]

    register_user(test_client, username="second")
    second_statistics = test_client.get("/api/statistics").json()
    assert second_statistics["completed_material_count"] == 0
    assert test_client.cookies.get("session_token") != first_user_cookie


def test_marking_unknown_material_returns_404(test_client: TestClient) -> None:
    register_user(test_client)

    response = test_client.post(
        "/api/statistics/materials",
        json={"article_id": "unknown"},
    )

    assert response.status_code == 404


def test_submit_file_validates_extension_and_size(test_client: TestClient) -> None:
    register_user(test_client)

    invalid_extension = test_client.post(
        "/api/submit-file",
        data={"question_id": "1"},
        files={"file": ("answer.txt", b"print('ok')", "text/plain")},
    )
    oversized_file = test_client.post(
        "/api/submit-file",
        data={"question_id": "1"},
        files={"file": ("answer.py", b"x" * 100_001, "text/x-python")},
    )

    assert invalid_extension.status_code == 400
    assert oversized_file.status_code == 400


def test_health_check_reports_ready(test_client: TestClient) -> None:
    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_revalidates_html_and_references_versioned_assets(test_client: TestClient) -> None:
    response = test_client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    assert "app.js?v=20261025-auth-cache" in response.text
    assert "styles.css?v=20261025-auth-cache" in response.text


def test_batch_difficulties_returns_unique_counts_for_requested_subjects(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    python_questions = create_test_questions(
        test_session_factory, count=2, subject="python", difficulty="easy"
    )
    create_test_questions(test_session_factory, count=3, subject="fastapi", difficulty="medium")
    with test_session_factory() as db:
        first_question = db.get(Question, python_questions[0]["question_id"])
        duplicate_question = db.get(Question, python_questions[1]["question_id"])
        assert first_question is not None
        assert duplicate_question is not None
        duplicate_question.title = first_question.title
        db.commit()

    response = test_client.get(
        "/api/difficulties/batch",
        params=[("subjects", "python"), ("subjects", "fastapi"), ("subjects", "python")],
    )

    assert response.status_code == 200
    difficulties = response.json()["difficulties"]
    assert set(difficulties) == {"python", "fastapi"}
    assert len(difficulties["python"]) == 5
    assert len(difficulties["fastapi"]) == 6
    assert next(item["question_count"] for item in difficulties["python"] if item["id"] == "easy") == 1
    assert next(item["question_count"] for item in difficulties["fastapi"] if item["id"] == "medium") == 3
    individual_response = test_client.get("/api/difficulties", params={"subject": "python"})
    assert next(
        item["question_count"]
        for item in individual_response.json()["difficulties"]
        if item["id"] == "easy"
    ) == 1


def test_batch_difficulties_reuses_cached_database_result(
    test_client: TestClient,
    test_session_factory: sessionmaker,
) -> None:
    create_test_questions(test_session_factory, count=2, subject="python", difficulty="easy")
    select_count = 0

    def count_selects(*args: object) -> None:
        nonlocal select_count
        if str(args[2]).lstrip().upper().startswith("SELECT"):
            select_count += 1

    bind = test_session_factory.kw["bind"]
    event.listen(bind, "before_cursor_execute", count_selects)
    try:
        params = [("subjects", "python"), ("subjects", "fastapi")]
        first_response = test_client.get("/api/difficulties/batch", params=params)
        second_response = test_client.get("/api/difficulties/batch", params=params)
    finally:
        event.remove(bind, "before_cursor_execute", count_selects)

    assert first_response.status_code == 200
    assert second_response.json() == first_response.json()
    assert select_count == 1
