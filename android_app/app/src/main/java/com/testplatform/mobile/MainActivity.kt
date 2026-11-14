package com.testplatform.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.gson.Gson
import com.testplatform.mobile.ui.theme.TestPlatformTheme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.CookieManager
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.JavaNetCookieJar
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.net.HttpURLConnection
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            TestPlatformTheme {
                TestPlatformApp()
            }
        }
    }
}

data class AuthResponse(
    val id: Int,
    val username: String,
    val email: String
)

data class DifficultyItem(
    val id: String,
    val label: String,
    val question_count: Int
)

data class DifficultyResponse(
    val difficulties: List<DifficultyItem>
)

data class QuestionOption(
    val id: Int,
    val label: String
)

data class Question(
    val id: Int,
    val title: String,
    val description: String,
    val question_type: String,
    val difficulty: String? = null,
    val points: Int,
    val starter_code: String? = null,
    val options: List<QuestionOption> = emptyList()
)

data class TestResponse(
    val title: String,
    val subtitle: String,
    val difficulty: String,
    val subject: String,
    val subject_label: String,
    val difficulty_label: String,
    val question_count: Int,
    val questions: List<Question>
)

data class SubmissionAnswer(
    val question_id: Int,
    val selected_option_ids: List<Int> = emptyList(),
    val answer_text: String? = null
)

data class SubmissionRequest(
    val difficulty: String,
    val subject: String,
    val answers: List<SubmissionAnswer>
)

data class ResultItem(
    val question_title: String,
    val is_correct: Boolean,
    val is_written: Boolean = false,
    val your_answer: String = "",
    val correct_answers: List<String> = emptyList(),
    val reference_answer: String = ""
)

data class SubmitResponse(
    val results: List<ResultItem>,
    val correct_count: Int = 0,
    val incorrect_count: Int = 0,
    val score: Int = 0,
    val max_score: Int = 0,
    val written_pending: Boolean = false,
    val score_percent: Int = 0,
    val correct_percent: Double = 0.0,
    val incorrect_percent: Double = 0.0
)

data class QuestionAnswerState(
    val selectedIds: List<Int> = emptyList(),
    val answerText: String = ""
)

private object ApiClient {
    private const val BASE_URL = "http://10.0.2.2:8000"
    private val gson = Gson()
    private val cookieJar = JavaNetCookieJar(CookieManager())
    val client = OkHttpClient.Builder().cookieJar(cookieJar).build()

    fun login(login: String, password: String): AuthResponse {
        val payload = mapOf("login" to login, "password" to password)
        val request = Request.Builder()
            .url("$BASE_URL/api/auth/login")
            .post(gson.toJson(payload).toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()

        return executeRequest(request, AuthResponse::class.java)
    }

    fun register(username: String, email: String, password: String): AuthResponse {
        val payload = mapOf(
            "username" to username,
            "email" to email,
            "password" to password
        )
        val request = Request.Builder()
            .url("$BASE_URL/api/auth/register")
            .post(gson.toJson(payload).toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()

        return executeRequest(request, AuthResponse::class.java)
    }

    fun getDifficulties(subject: String): List<DifficultyItem> {
        val url = "$BASE_URL/api/difficulties?subject=$subject".toHttpUrl()
        val request = Request.Builder().url(url).get().build()
        val response = executeRequestInternal(request)
        val difficultyResponse = gson.fromJson(response, DifficultyResponse::class.java)
        return difficultyResponse.difficulties
    }

    fun getTest(difficulty: String, subject: String): TestResponse {
        val url = "$BASE_URL/api/test?difficulty=$difficulty&subject=$subject".toHttpUrl()
        val request = Request.Builder().url(url).get().build()
        val response = executeRequestInternal(request)
        return gson.fromJson(response, TestResponse::class.java)
    }

    fun submit(payload: SubmissionRequest): SubmitResponse {
        val request = Request.Builder()
            .url("$BASE_URL/api/submit")
            .post(gson.toJson(payload).toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()
        val response = executeRequestInternal(request)
        return gson.fromJson(response, SubmitResponse::class.java)
    }

    private fun executeRequestInternal(request: Request): String {
        val response = client.newCall(request).execute()
        val body = response.body?.string() ?: "{}"
        if (!response.isSuccessful) {
            val detail = try {
                gson.fromJson(body, Map::class.java)?.get("detail").toString()
            } catch (_: Exception) {
                body
            }
            throw IllegalStateException(detail.ifEmpty { "Ошибка запроса: ${response.code}" })
        }
        return body
    }

    private fun <T> executeRequest(request: Request, type: Class<T>): T {
        val body = executeRequestInternal(request)
        return gson.fromJson(body, type)
    }
}

@Composable
fun TestPlatformApp() {
    var authUser by remember { mutableStateOf<AuthResponse?>(null) }
    var currentSubject by remember { mutableStateOf("python") }
    var currentDifficulty by remember { mutableStateOf<String?>(null) }
    var difficulties by remember { mutableStateOf<Map<String, List<DifficultyItem>>>(emptyMap()) }
    var currentTest by remember { mutableStateOf<TestResponse?>(null) }
    var submitResult by remember { mutableStateOf<SubmitResponse?>(null) }
    var errorText by remember { mutableStateOf("") }
    val answers = remember { mutableStateMapOf<Int, QuestionAnswerState>() }
    var authMode by remember { mutableStateOf("login") }
    var loginValue by remember { mutableStateOf("") }
    var emailValue by remember { mutableStateOf("") }
    var passwordValue by remember { mutableStateOf("") }

    fun refreshDifficulties() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val map = linkedMapOf<String, List<DifficultyItem>>()
                for (subject in listOf("python", "fastapi", "aiogram")) {
                    map[subject] = ApiClient.getDifficulties(subject)
                }
                difficulties = map
            } catch (e: Exception) {
                errorText = e.message ?: "Не удалось загрузить уровни"
            }
        }
    }

    fun loadTest(difficulty: String, subject: String) {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                currentDifficulty = difficulty
                currentSubject = subject
                currentTest = ApiClient.getTest(difficulty, subject)
                submitResult = null
                answers.clear()
                errorText = ""
            } catch (e: Exception) {
                errorText = e.message ?: "Не удалось загрузить тест"
            }
        }
    }

    fun submitAnswers() {
        val test = currentTest ?: return
        val payloadAnswers = test.questions.map { question ->
            val state = answers[question.id] ?: QuestionAnswerState()
            SubmissionAnswer(
                question_id = question.id,
                selected_option_ids = state.selectedIds,
                answer_text = if (question.question_type == "written") state.answerText else null
            )
        }
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val payload = SubmissionRequest(
                    difficulty = currentDifficulty ?: test.difficulty,
                    subject = currentSubject,
                    answers = payloadAnswers
                )
                submitResult = ApiClient.submit(payload)
                errorText = ""
            } catch (e: Exception) {
                errorText = e.message ?: "Не удалось отправить ответы"
            }
        }
    }

    when {
        authUser == null -> {
            AuthScreen(
                mode = authMode,
                loginValue = loginValue,
                emailValue = emailValue,
                passwordValue = passwordValue,
                onLoginValueChange = { loginValue = it },
                onEmailValueChange = { emailValue = it },
                onPasswordValueChange = { passwordValue = it },
                onSwitchMode = { authMode = if (authMode == "login") "register" else "login" },
                errorText = errorText,
                onSubmit = {
                    CoroutineScope(Dispatchers.IO).launch {
                        try {
                            val user = if (authMode == "login") {
                                ApiClient.login(loginValue.trim(), passwordValue.trim())
                            } else {
                                ApiClient.register(loginValue.trim(), emailValue.trim(), passwordValue.trim())
                            }
                            authUser = user
                            errorText = ""
                            refreshDifficulties()
                        } catch (e: Exception) {
                            errorText = e.message ?: "Ошибка входа"
                        }
                    }
                }
            )
        }
        submitResult != null -> {
            ResultScreen(
                result = submitResult!!,
                onBackToSubjects = {
                    submitResult = null
                    currentTest = null
                    currentDifficulty = null
                }
            )
        }
        currentTest != null -> {
            TestScreen(
                test = currentTest!!,
                answers = answers,
                onAnswerChange = { questionId, state ->
                    answers[questionId] = state
                },
                onSubmit = { submitAnswers() },
                onBack = {
                    currentTest = null
                    currentDifficulty = null
                }
            )
        }
        else -> {
            SubjectSelectionScreen(
                user = authUser!!,
                difficulties = difficulties,
                currentSubject = currentSubject,
                onSelectSubject = { subject -> currentSubject = subject },
                onSelectDifficulty = { subject, difficulty -> loadTest(difficulty, subject) },
                onLogout = { authUser = null; loginValue = ""; emailValue = ""; passwordValue = ""; difficulties = emptyMap(); currentTest = null; submitResult = null },
                errorText = errorText
            )
        }
    }

    LaunchedEffect(authUser) {
        if (authUser != null) {
            refreshDifficulties()
        }
    }
}

@Composable
fun AuthScreen(
    mode: String,
    loginValue: String,
    emailValue: String,
    passwordValue: String,
    onLoginValueChange: (String) -> Unit,
    onEmailValueChange: (String) -> Unit,
    onPasswordValueChange: (String) -> Unit,
    onSwitchMode: () -> Unit,
    errorText: String,
    onSubmit: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFFF5F7FF))
            .padding(24.dp),
        verticalArrangement = Arrangement.Center
    ) {
        Text(text = "Python Test Lab", fontSize = 28.sp, fontWeight = FontWeight.Bold)
        Text(text = "Мобильный клиент для тестов и практики", color = Color.Gray, modifier = Modifier.padding(bottom = 24.dp))
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(20.dp)) {
                if (mode == "register") {
                    OutlinedTextField(
                        value = loginValue,
                        onValueChange = onLoginValueChange,
                        label = { Text("Имя пользователя") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    OutlinedTextField(
                        value = emailValue,
                        onValueChange = onEmailValueChange,
                        label = { Text("Email") },
                        modifier = Modifier.fillMaxWidth()
                    )
                } else {
                    OutlinedTextField(
                        value = loginValue,
                        onValueChange = onLoginValueChange,
                        label = { Text("Логин или email") },
                        modifier = Modifier.fillMaxWidth()
                    )
                }
                Spacer(modifier = Modifier.height(12.dp))
                OutlinedTextField(
                    value = passwordValue,
                    onValueChange = onPasswordValueChange,
                    label = { Text("Пароль") },
                    modifier = Modifier.fillMaxWidth()
                )
                if (errorText.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(12.dp))
                    Text(text = errorText, color = Color.Red)
                }
                Spacer(modifier = Modifier.height(16.dp))
                Button(
                    onClick = onSubmit,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(text = if (mode == "login") "Войти" else "Создать аккаунт")
                }
                Spacer(modifier = Modifier.height(12.dp))
                OutlinedButton(
                    onClick = onSwitchMode,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text(text = if (mode == "login") "Нет аккаунта? Зарегистрироваться" else "Уже есть аккаунт? Войти")
                }
            }
        }
    }
}

@Composable
fun SubjectSelectionScreen(
    user: AuthResponse,
    difficulties: Map<String, List<DifficultyItem>>,
    currentSubject: String,
    onSelectSubject: (String) -> Unit,
    onSelectDifficulty: (String, String) -> Unit,
    onLogout: () -> Unit,
    errorText: String
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFFF5F7FF))
            .padding(16.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(text = "Вы вошли как ${user.username}", fontWeight = FontWeight.SemiBold)
            OutlinedButton(onClick = onLogout) { Text("Выйти") }
        }
        Spacer(modifier = Modifier.height(12.dp))
        Text(text = "Выберите тему и уровень", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        Spacer(modifier = Modifier.height(12.dp))
        val subjects = listOf("python" to "Python", "fastapi" to "FastAPI", "aiogram" to "aiogram")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            subjects.forEach { (subject, label) ->
                val selected = subject == currentSubject
                Button(
                    onClick = { onSelectSubject(subject) },
                    modifier = Modifier.weight(1f),
                    enabled = true
                ) {
                    Text(text = label)
                }
            }
        }
        Spacer(modifier = Modifier.height(12.dp))
        if (errorText.isNotEmpty()) {
            Text(text = errorText, color = Color.Red)
            Spacer(modifier = Modifier.height(12.dp))
        }

        val list = difficulties[currentSubject].orEmpty()
        if (list.isEmpty()) {
            Text("Загрузка уровней...")
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                itemsIndexed(list) { _, item ->
                    val enabled = item.question_count >= 20
                    Card(
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(text = item.label, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                            Text(text = "${item.question_count} уникальных вопросов")
                            Spacer(modifier = Modifier.height(12.dp))
                            Button(
                                onClick = { if (enabled) onSelectDifficulty(currentSubject, item.id) },
                                enabled = enabled,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Text(text = if (enabled) "Начать тест" else "Недоступно")
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun TestScreen(
    test: TestResponse,
    answers: Map<Int, QuestionAnswerState>,
    onAnswerChange: (Int, QuestionAnswerState) -> Unit,
    onSubmit: () -> Unit,
    onBack: () -> Unit
) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFFF5F7FF))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(text = test.subject_label, fontWeight = FontWeight.Bold)
                OutlinedButton(onClick = onBack) { Text("Назад") }
            }
            Text(text = test.title, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Text(text = test.subtitle, color = Color.Gray)
            Spacer(modifier = Modifier.height(8.dp))
            Text(text = "${test.question_count} заданий в попытке")
        }

        itemsIndexed(test.questions) { index, question ->
            val state = answers[question.id] ?: QuestionAnswerState()
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(text = "Задание ${index + 1} / ${question.points} баллов", fontWeight = FontWeight.SemiBold)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(text = question.title, fontWeight = FontWeight.Bold)
                    if (question.description.isNotBlank()) {
                        Text(text = question.description, color = Color.DarkGray)
                    }
                    Spacer(modifier = Modifier.height(12.dp))

                    when (question.question_type) {
                        "written" -> {
                            OutlinedTextField(
                                value = state.answerText,
                                onValueChange = { value -> onAnswerChange(question.id, state.copy(answerText = value)) },
                                modifier = Modifier.fillMaxWidth(),
                                label = { Text("Введите ответ") }
                            )
                        }
                        "single" -> {
                            question.options.forEach { option ->
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .selectable(
                                            selected = state.selectedIds.contains(option.id),
                                            onClick = {
                                                onAnswerChange(question.id, QuestionAnswerState(selectedIds = listOf(option.id)))
                                            }
                                        )
                                        .padding(vertical = 6.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    RadioButton(
                                        selected = state.selectedIds.contains(option.id),
                                        onClick = { onAnswerChange(question.id, QuestionAnswerState(selectedIds = listOf(option.id))) }
                                    )
                                    Spacer(modifier = Modifier.width(8.dp))
                                    Text(option.label)
                                }
                            }
                        }
                        else -> {
                            question.options.forEach { option ->
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 4.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Checkbox(
                                        checked = state.selectedIds.contains(option.id),
                                        onCheckedChange = { checked ->
                                            val ids = state.selectedIds.toMutableList()
                                            if (checked) {
                                                ids.add(option.id)
                                            } else {
                                                ids.remove(option.id)
                                            }
                                            onAnswerChange(question.id, QuestionAnswerState(selectedIds = ids))
                                        }
                                    )
                                    Spacer(modifier = Modifier.width(8.dp))
                                    Text(option.label)
                                }
                            }
                        }
                    }
                }
            }
        }

        item {
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = onSubmit,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Проверить тест")
            }
        }
    }
}

@Composable
fun ResultScreen(
    result: SubmitResponse,
    onBackToSubjects: () -> Unit
) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFFF5F7FF))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item {
            Text(text = "Результат: ${result.score} / ${result.max_score}", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Text(text = "Правильные: ${result.correct_count} | Неправильные: ${result.incorrect_count}")
            if (result.written_pending) {
                Text(text = "Письменное задание отправлено на ручную проверку")
            }
        }

        itemsIndexed(result.results) { index, item ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(text = "${index + 1}. ${item.question_title}", fontWeight = FontWeight.SemiBold)
                    Text(text = if (item.is_correct) "Верно" else "Проверьте")
                    Text(text = "Ваш ответ: ${item.your_answer.ifBlank { '—'.toString() }}")
                    val correctString = if (item.correct_answers.isNotEmpty()) item.correct_answers.joinToString(", ") else item.reference_answer
                    Text(text = "Правильный ответ: ${correctString.ifBlank { "Проверяется вручную" }}")
                }
            }
        }

        item {
            Button(
                onClick = onBackToSubjects,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Вернуться к темам")
            }
        }
    }
}
