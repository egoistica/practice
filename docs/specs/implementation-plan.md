I have created the following plan after thorough exploration and analysis of the codebase. Follow the below plan verbatim. Trust the files and references. Do not re-verify what's written in the plan. Explore only when absolutely necessary. First implement all the proposed file changes and then I'll review all the changes together at the end.

# План реализации системы создания конспектов и графа сущностей

## Наблюдения

Проект начинается с нуля — рабочая директория пуста. Требуется система: видео → транскрипция → конспект → граф сущностей, с web и Telegram-интерфейсами, мультипользовательским режимом и администраторской панелью. Для успешной работы команды требуется разбиение на атомарные задачи с чёткими deliverables.

## Подход

Используем **FastAPI** (async backend) + **React** (SPA) + **PostgreSQL** + **Redis** + **Celery** + **aiogram 3** (Telegram-бот). Разбиение на фазы: инфраструктура → БД → auth → core API → обработка видео → LLM → интерфейсы → admin-функционал → монетизация. Каждая задача атомарна и тестируема.

---

## ФАЗА 1: Инфраструктура и базовая конфигурация

### Задача 1.1 — Инициализация проекта и Docker
**Deliverables:**
- Создать структуру директорий: `backend/`, `frontend/`, `telegram_bot/`, `media/`, `docker-compose.yml`, `.env.example`
- Написать `docker-compose.yml` с сервисами: postgres (16), redis (latest), backend (FastAPI), celery_worker, frontend (React), telegram_bot
- Создать `.env.example` с переменными: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `LLM_PROVIDER`, `MEDIA_ROOT`
- Dockerfile для backend и worker'а
- `requirements.txt` для backend с базовыми пакетами

**Критерии готовности:**
- `docker-compose up` запускает все сервисы без ошибок
- Postgres доступен на `localhost:5432`
- Redis на `localhost:6379`

---

### Задача 1.2 — Настройка FastAPI приложения и зависимостей
**Deliverables:**
- Файл `backend/app/__init__.py`
- `backend/app/main.py` — точка входа приложения (FastAPI instance, CORS, middleware)
- `backend/app/core/config.py` — Pydantic settings для окружения
- `backend/requirements.txt` с зависимостями:
  - `fastapi==0.104.1`
  - `sqlalchemy==2.0.23`
  - `asyncpg==0.29.0`
  - `celery==5.3.4`
  - `redis==5.0.1`
  - `python-jose[cryptography]==3.3.0`
  - `passlib[bcrypt]==1.7.4`
  - `pydantic==2.5.0`
  - `pydantic-settings==2.1.0`
  - `python-dotenv==1.0.0`
  - `litellm==1.6.0`
  - `openai==1.3.0`
  - `aiogram==3.3.0`
  - `yt-dlp==2023.12.30`
  - `faster-whisper==0.10.0`
  - `ffmpeg-python==0.2.1`
  - `requests==2.31.0`
  - `uvicorn==0.24.0`
  - `slowapi==0.1.9`

**Критерии готовности:**
- `python -m backend.app.main` запускает сервер без ошибок на `http://localhost:8000`
- `/docs` (Swagger) доступна
- Все импорты работают

---

### Задача 1.3 — Инициализация Celery и Redis
**Deliverables:**
- `backend/celery_app.py` — конфигурация Celery с Redis broker и backend
- `backend/app/core/celery_config.py` — класс конфигурации Celery
- Интеграция Celery в FastAPI через dependency injection

**Критерии готовности:**
- `celery -A backend.celery_app worker --loglevel=info` запускается без ошибок
- Команда `celery -A backend.celery_app inspect active` показывает worker'а

---

## ФАЗА 2: Модели БД и миграции

### Задача 2.1 — Настройка SQLAlchemy и Alembic
**Deliverables:**
- `backend/app/core/database.py` — async engine, sessionmaker, Base class для моделей
- Инициализация Alembic: `alembic init -t async backend/app/db/migrations`
- `backend/app/db/migrations/env.py` — конфигурация для работы с async SQLAlchemy
- Скрипт `backend/app/db/init_db.py` для создания таблиц вручную (на случай проблем с миграциями)

**Критерии готовности:**
- `alembic upgrade head` успешно запускается
- Таблицы создаются в PostgreSQL
- Команда `alembic revision --autogenerate -m "Initial migration"` работает

---

### Задача 2.2 — Модели пользователей и аутентификации
**Deliverables:**
- `backend/app/models/user.py`:
  ```python
  class User (SQLAlchemy ORM model):
    - id: UUID primary key
    - username: str unique
    - email: str unique
    - hashed_password: str
    - is_admin: bool default=False
    - is_active: bool default=True
    - token_balance: int default=1000
    - created_at: datetime
    - updated_at: datetime
  ```
- Индексы на `username`, `email`
- Alembic миграция

**Критерии готовности:**
- Таблица `user` создана в БД
- ORM модель импортируется без ошибок

---

### Задача 2.3 — Модели лекций, конспектов, графов
**Deliverables:**
- `backend/app/models/lecture.py`:
  ```python
  class Lecture:
    - id: UUID primary key
    - user_id: UUID foreign key -> User
    - title: str
    - source_type: enum (file, url)
    - source_url: str nullable
    - file_path: str nullable (путь к загруженному видео)
    - thumbnail_path: str nullable
    - duration: float nullable (в секундах)
    - status: enum (pending, processing, done, error) default=pending
    - mode: enum (instant, realtime) default=instant
    - processing_progress: int default=0 (0-100%)
    - error_message: str nullable
    - created_at: datetime
    - updated_at: datetime
  ```
- `backend/app/models/transcript.py`:
  ```python
  class Transcript:
    - id: UUID primary key
    - lecture_id: UUID foreign key -> Lecture
    - segments: JSONB (list of {timestamp, text, speaker})
    - full_text: text
    - created_at: datetime
  ```
- `backend/app/models/summary.py`:
  ```python
  class Summary:
    - id: UUID primary key
    - lecture_id: UUID foreign key -> Lecture
    - content: JSONB (list of blocks with title, text, type: thought/definition/date/conclusion)
    - timecode_start: float nullable
    - timecode_end: float nullable
    - created_at: datetime
  ```
- `backend/app/models/entity_graph.py`:
  ```python
  class EntityGraph:
    - id: UUID primary key
    - lecture_id: UUID foreign key -> Lecture
    - nodes: JSONB (list of {id, label, type, enriched, mentions: [(position, timecode)]})
    - edges: JSONB (list of {source, target, label})
    - enriched: bool default=False
    - created_at: datetime
  ```
- Миграции Alembic

**Критерии готовности:**
- 4 новые таблицы созданы в БД с правильными внешними ключами
- ORM модели импортируются

---

### Задача 2.4 — Модели истории, избранного, сессий, транзакций токенов
**Deliverables:**
- `backend/app/models/favourite.py`:
  ```python
  class Favourite:
    - id: UUID primary key
    - user_id: UUID foreign key -> User
    - lecture_id: UUID foreign key -> Lecture
    - created_at: datetime
    - unique constraint (user_id, lecture_id)
  ```
- `backend/app/models/history.py`:
  ```python
  class History:
    - id: UUID primary key
    - user_id: UUID foreign key -> User
    - lecture_id: UUID foreign key -> Lecture
    - visited_at: datetime
    - unique constraint на (user_id, lecture_id, date(visited_at))
  ```
- `backend/app/models/user_session.py`:
  ```python
  class UserSession:
    - id: UUID primary key
    - user_id: UUID foreign key -> User
    - created_at: datetime
    - ip: str
  ```
- `backend/app/models/token_transaction.py`:
  ```python
  class TokenTransaction:
    - id: UUID primary key
    - user_id: UUID foreign key -> User
    - amount: int (может быть отрицательным)
    - reason: str
    - created_at: datetime
  ```
- Миграции

**Критерии готовности:**
- 4 новые таблицы с правильными индексами
- Все модели интегрированы в `backend/app/models/__init__.py`

---

## ФАЗА 3: Аутентификация и управление пользователями

### Задача 3.1 — Утилиты безопасности (хеширование, JWT)
**Deliverables:**
- `backend/app/core/security.py`:
  - Функция `hash_password(password: str) -> str` через `passlib[bcrypt]`
  - Функция `verify_password(plain: str, hashed: str) -> bool`
  - Функции `create_access_token(data: dict, expires_delta: timedelta) -> str`
  - Функция `create_refresh_token(user_id: UUID) -> str`
  - Функция `decode_token(token: str) -> dict`
- `backend/app/core/config.py` дополнить: `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`

**Критерии готовности:**
- Все функции работают корректно (покрыть unit-тестами)
- Токены содержат `exp` и `user_id` claims

---

### Задача 3.2 — Зависимости FastAPI для аутентификации
**Deliverables:**
- `backend/app/core/dependencies.py`:
  - `get_current_user(token: str = Depends(HTTPBearer)) -> User` — декодирует JWT, возвращает пользователя из БД
  - `require_admin(user: User = Depends(get_current_user)) -> User` — проверяет `is_admin`
  - `get_db()` — async session dependency
- Кастомное исключение `HTTPException` для 401/403

**Критерии готовности:**
- Dependencies работают в FastAPI роутерах
- Неавторизованные запросы возвращают 401

---

### Задача 3.3 — API: Регистрация и вход
**Deliverables:**
- `backend/app/schemas/auth.py`:
  ```python
  class RegisterRequest:
    - username: str
    - email: str
    - password: str

  class LoginRequest:
    - username: str (или email)
    - password: str

  class TokenResponse:
    - access_token: str
    - refresh_token: str
    - token_type: str = "bearer"
    - user_id: UUID
  ```
- `backend/app/api/auth.py` роутер:
  - `POST /auth/register` — создание пользователя, валидация уникальности, возврат токенов
  - `POST /auth/login` — проверка пароля, возврат токенов
  - `POST /auth/refresh` — обновление access токена через refresh токен
- Обработка ошибок (duplicate username, wrong password)

**Критерии готовности:**
- Curl команды работают: создание пользователя → вход → получение данных с JWT
- Refresh токен работает

---

### Задача 3.4 — API: Admin управление пользователями
**Deliverables:**
- `backend/app/api/admin/users.py` роутер:
  - `GET /admin/users` — список всех пользователей (пагинация, фильтр по активности)
  - `POST /admin/users` — создание нового пользователя (админ может выбрать пароль/сгенерировать)
  - `PATCH /admin/users/{user_id}` — изменение статуса (is_active, is_admin)
  - `DELETE /admin/users/{user_id}` — деактивация пользователя
  - `POST /admin/users/{user_id}/tokens` — добавление токенов
- Логирование действий admin в `UserSession` или отдельной таблице

**Критерии готовности:**
- Admin может создать пользователя и подтвердить в БД
- Деактивированный пользователь не может логиниться

---

## ФАЗА 4: Ядро API — управление лекциями

### Задача 4.1 — API: Загрузка и создание лекции
**Deliverables:**
- `backend/app/schemas/lecture.py`:
  ```python
  class CreateLectureRequest:
    - title: str
    - mode: enum (instant, realtime)
    - source_type: enum (file, url)
    - source_url: str nullable
    - selected_entities: list[str] nullable (какие сущности искать)

  class LectureResponse:
    - id: UUID
    - title: str
    - status: str
    - processing_progress: int
    - created_at: datetime
  ```
- `backend/app/api/lectures.py` роутер:
  - `POST /lectures` — принять файл (MP4, AVI, MKV, MOV) или URL; создать запись в Lecture; сохранить файл; вернуть lecture_id
  - `GET /lectures` — список всех лекций пользователя (пагинация, сортировка по дате)
  - `GET /lectures/{id}` — детали одной лекции с прогрессом обработки
  - `DELETE /lectures/{id}` — удаление лекции и файлов
- `backend/app/services/file_service.py` — утилиты: валидация MIME-типа, сохранение в `media/{lecture_id}/`, генерация UUID-имён

**Критерии готовности:**
- Файл загружается и сохраняется на диск
- Запись в БД создаётся
- GET эндпоинт возвращает корректные данные

---

### Задача 4.2 — WebSocket для прогресса обработки
**Deliverables:**
- `backend/app/api/lectures.py` добавить:
  - `WS /ws/{lecture_id}` — WebSocket эндпоинт
  - Функция `broadcast_progress(lecture_id: UUID, progress: int)` — отправляет обновления по WS и Redis Pub/Sub
- Хранение активных подписок на WebSocket в памяти (или через Redis для масштабирования)
- При обновлении статуса Lecture в БД → публикация через WS

**Критерии готовности:**
- Web-клиент может подписаться на `WS ws://localhost:8000/ws/{lecture_id}`
- При обновлении статуса в Celery worker'е → клиент получает сообщение

---

### Задача 4.3 — API: История и Избранное
**Deliverables:**
- `backend/app/api/favourites.py`:
  - `GET /favourites` — список избранных лекций пользователя
  - `POST /favourites/{lecture_id}` — добавить в избранное
  - `DELETE /favourites/{lecture_id}` — удалить из избранного
- `backend/app/api/history.py`:
  - `GET /history` — список просмотров (пагинация, сортировка по дате)
  - Middleware или dependency для автоматического добавления записи при `GET /lectures/{id}`
- Дедупликация в истории (максимум одна запись в день на лекцию)

**Критерии готовности:**
- Endpoints работают
- При повторном открытии лекции история не дублируется

---

## ФАЗА 5: Pipeline обработки видео (Celery Tasks)

### Задача 5.1 — Утилиты для работы с видео и аудио
**Deliverables:**
- `backend/app/services/video_service.py`:
  - `download_video(url: str, output_path: str) -> str` — yt-dlp для YouTube/VK, возвращает путь к файлу
  - `extract_audio(video_path: str, output_path: str) -> str` — FFmpeg извлечение аудио в WAV, возвращает путь
  - `get_video_duration(video_path: str) -> float` — длительность видео в секундах
  - `get_video_thumbnail(video_path: str, output_path: str) -> str` — снятие скриншота
- Обработка ошибок (неподдерживаемый формат, недоступный URL)

**Критерии готовности:**
- Функции работают с тестовыми видеофайлами
- Аудио правильно извлекается

---

### Задача 5.2 — Транскрипция (Whisper)
**Deliverables:**
- `backend/app/services/transcription_service.py`:
  - `transcribe_audio(audio_path: str, language: str = "ru") -> dict` — faster-whisper с сегментацией
  - Возвращает: `{segments: [{start: float, end: float, text: str}, ...], full_text: str}`
  - Поддержка русского языка
  - Обработка ошибок (повреждённый аудиофайл)
- Модель Whisper скачивается один раз и кешируется

**Критерии готовности:**
- Транскрипция рабочего аудиофайла работает
- Таймкоды правильные

---

### Задача 5.3 — Text Segmentation (разбивка текста)
**Deliverables:**
- `backend/app/services/text_processing.py`:
  - `segment_text(text: str, segments: list[dict]) -> list[dict]` — разбивка текста на логические блоки по таймкодам и/или句末 (конец предложения/абзаца)
  - Может быть простая реализация (разбивка по интервалам 30-60 сек) или с использованием простого NLP
  - Возвращает: `[{timecode_start, timecode_end, text}, ...]`

**Критерии готовности:**
- Текст разбивается на блоки ~100-300 слов
- Таймкоды привязаны к оригинальным сегментам

---

### Задача 5.4 — Summary генерация (LLM)
**Deliverables:**
- `backend/app/services/llm_service.py`:
  - `summarize_segment(text: str, llm_config: dict) -> dict` — запрос к LiteLLM
  - Промпт: "Суммаризируй этот текст лекции, выделяя главные мысли, определения, даты, выводы. Структурируй как список логических блоков."
  - Парсинг ответа в JSON: `{blocks: [{title: str, text: str, type: "thought"|"definition"|"date"|"conclusion"}, ...]}`
  - Поддержка разных LLM провайдеров через LiteLLM (OpenAI, Ollama, локальные)
- `backend/app/core/config.py` добавить: `LLM_PROVIDER`, `LLM_MODEL`, `OLLAMA_BASE_URL` (если локально)

**Критерии готовности:**
- LLM запрос работает с любым поддерживаемым провайдером
- Ответ правильно парсится в JSON

---

### Задача 5.5 — Entity Extraction (NER + граф)
**Deliverables:**
- `backend/app/services/llm_service.py` добавить:
  - `extract_entities(text: str, selected_entities: list[str] = None, llm_config: dict) -> dict` — запрос к LLM на NER
  - Промпт: "Найди именованные сущности в тексте: термины, персоналии, теории. Для каждой сущности найди связи с другими сущностями. Вернись JSON с узлами {id, label, type} и рёбрами {source, target, label}. Если указаны нужные сущности [список], фокусируйся на них."
  - Фильтрация сущностей по `selected_entities` если передано
  - Дедупликация узлов по label (нормализация)
- JSON структура: `{nodes: [{id, label, type, mentions: [{position_in_text, timecode}]}, ...], edges: [{source, target, label}, ...]}`

**Критерии готовности:**
- NER работает и возвращает корректные JSON
- Связи между сущностями логичны

---

### Задача 5.6 — Обогащение информации (дополнительные данные)
**Deliverables:**
- `backend/app/services/llm_service.py` добавить:
  - `enrich_graph(nodes: list[dict], edges: list[dict], llm_config: dict) -> dict` — расширение графа
  - Промпт: "Даны сущности [список] и их связи. Добавь релевантные сущности и связи, которые логически связаны, но не упомянуты в исходном тексте. Пометь новые узлы флагом 'enriched': true. Верни расширенный JSON."
  - Мерджинг результата с исходным графом (дедупликация)
- API: `POST /lectures/{id}/graph/enrich` — опциональный запрос пользователя
- Флаг `enriched: true` в модели `EntityGraph`

**Критерии готовности:**
- Обогащение добавляет разумные новые узлы/рёбра
- Исходные узлы не дублируются

---

### Задача 5.7 — Celery Chain: главная задача обработки
**Deliverables:**
- `backend/app/tasks/process_lecture.py`:
  ```python
  @shared_task(bind=True)
  def process_lecture_chain(self, lecture_id: UUID, selected_entities: list = None):
    # Цепь задач: download → extract_audio → transcribe → segment → summarize → extract_entities → save_results
    chain = (
      download_video_task.s(lecture_id) |
      extract_audio_task.s(lecture_id) |
      transcribe_task.s(lecture_id) |
      segment_text_task.s(lecture_id) |
      summarize_task.s(lecture_id) |
      extract_entities_task.s(lecture_id, selected_entities) |
      save_results_task.s(lecture_id)
    )
    chain.apply_async()
  ```
- Каждая задача обновляет `Lecture.processing_progress` и публикует прогресс через WebSocket/Redis
- Обработка ошибок: при падении задачи → сохранение ошибки в `Lecture.error_message`, статус → "error"

**Критерии готовности:**
- Цепь выполняется от начала до конца
- Прогресс отображается корректно
- Ошибки обрабатываются и логируются

---

### Задача 5.8 — Realtime режим (обработка по мере просмотра)
**Deliverables:**
- Модификация `process_lecture_chain` для режима `mode: realtime`:
  - После каждого N-секундного сегмента транскрипции (например, 60 сек):
    - Запуск `summarize_task` на текущий segment
    - Запуск `extract_entities_task`
    - Публикация результата через WebSocket с таймкодом
  - Web-клиент подписан на WS и постепенно рендерит блоки конспекта и узлы графа
- Флаг `realtime_mode` во время обработки в `Lecture`

**Критерии готовности:**
- В realtime режиме блоки появляются по мере обработки
- Таймкоды привязаны корректно

---

## ФАЗА 6: API для конспектов и графов

### Задача 6.1 — Endpoints для конспекта
**Deliverables:**
- `backend/app/schemas/summary.py`:
  ```python
  class SummaryBlock:
    - title: str
    - text: str
    - type: str
    - timecode_start: float nullable
    - timecode_end: float nullable

  class SummaryResponse:
    - id: UUID
    - blocks: list[SummaryBlock]
    - enriched: bool
  ```
- `backend/app/api/lectures.py` добавить:
  - `GET /lectures/{id}/summary` — возвращает конспект с блоками
  - `GET /lectures/{id}/transcript` — возвращает полный текст транскрипции с таймкодами
  - `POST /lectures/{id}/summary/enrich` — добавление расширяющей информации (галочка в интерфейсе)

**Критерии готовности:**
- Endpoints возвращают JSON с правильной структурой
- Пользователь без доступа (чужая лекция) получает 403

---

### Задача 6.2 — Endpoints для графа
**Deliverables:**
- `backend/app/schemas/graph.py`:
  ```python
  class Node:
    - id: str
    - label: str
    - type: str
    - enriched: bool
    - mentions: list[{position, timecode}]

  class Edge:
    - source: str
    - target: str
    - label: str

  class GraphResponse:
    - nodes: list[Node]
    - edges: list[Edge]
  ```
- `backend/app/api/lectures.py` добавить:
  - `GET /lectures/{id}/graph` — возвращает граф (узлы + рёбра)
  - `POST /lectures/{id}/graph/enrich` — добавление расширяющей информации
  - Поле `graph.enriched: bool` отражает состояние обогащения

**Критерии готовности:**
- Graph JSON структурирован для D3/Vis.js
- При клике на узел → можно найти упоминания в конспекте

---

### Задача 6.3 — Сохранение и экспорт (конспект)
**Deliverables:**
- `backend/app/services/export_service.py`:
  - `export_summary_to_markdown(summary: Summary) -> str` — конспект в Markdown
  - `export_summary_to_pdf(summary: Summary) -> bytes` — через `reportlab` или `weasyprint`
  - `export_summary_to_json(summary: Summary) -> str`
- `backend/app/api/lectures.py` добавить:
  - `GET /lectures/{id}/export?format=md|pdf|json` — скачивание в выбранном формате

**Критерии готовности:**
- Скачивание работает, файлы корректно форматированы

---

### Задача 6.4 — Сохранение и экспорт (граф)
**Deliverables:**
- `backend/app/services/export_service.py`:
  - `export_graph_to_json(graph: EntityGraph) -> str` — JSON nodes/edges
  - `export_graph_to_image(graph: EntityGraph) -> bytes` — PNG через `networkx` + `matplotlib` (для бота)
- `backend/app/api/lectures.py` добавить:
  - `GET /lectures/{id}/graph/export?format=json|png`

**Критерии готовности:**
- JSON экспорт работает
- PNG генерируется и отправляется как файл

---

## ФАЗА 7: Web-интерфейс (React)

### Задача 7.1 — Инициализация React приложения
**Deliverables:**
- `npm create vite@latest frontend -- --template react-ts`
- `frontend/package.json` добавить зависимости:
  - `axios` — HTTP клиент
  - `react-router-dom` — маршрутизация
  - `react-query` — управление состоянием (альтернатива Redux)
  - `vis-network` или `react-vis-graph` — граф
  - `recharts` — графики
  - `tailwindcss` — стили
  - `zustand` — глобальное состояние (опционально)
- `frontend/src/api/client.ts` — axios instance с JWT авторизацией
- `frontend/src/hooks/useAuth.ts` — хук для управления авторизацией

**Критерии готовности:**
- `npm install && npm run dev` запускает сервер на `localhost:5173`
- Все импорты работают

---

### Задача 7.2 — Страницы auth (login, register)
**Deliverables:**
- `frontend/src/pages/Login.tsx` — форма входа (username/password)
- `frontend/src/pages/Register.tsx` — форма регистрации
- Сохранение токена в localStorage
- Редирект на `/dashboard` после успешного входа
- Обработка ошибок (неверный пароль, пользователь существует)

**Критерии готовности:**
- Регистрация и вход работают
- Токены сохраняются в localStorage и используются в API запросах

---

### Задача 7.3 — Страница Dashboard (список лекций)
**Deliverables:**
- `frontend/src/pages/Dashboard.tsx`:
  - Список лекций пользователя (карточки или таблица)
  - Каждая карточка: название, статус, дата, иконка "избранное" (звезда)
  - Поиск по названию, фильтр по статусу
  - Кнопка "Загрузить новую лекцию" → `/upload`
- `frontend/src/components/LectureCard.tsx` — переиспользуемый компонент карточки

**Критерии готовности:**
- Dashboard загружает список лекций через API
- Звёздочка переключает избранное

---

### Задача 7.4 — Страница Upload (загрузка видео)
**Deliverables:**
- `frontend/src/pages/Upload.tsx`:
  - Drag-and-drop или file input для загрузки файла (MP4, AVI, MKV, MOV)
  - Поле для ввода URL (YouTube, VK Video)
  - Выбор режима: "Мгновенный конспект" (instant) или "По мере просмотра" (realtime)
  - Multi-select для выбора нужных сущностей (или оставить пусто для автоматического выбора)
  - Кнопка "Загрузить" → POST запрос, редирект на `/lecture/{id}`
- Валидация: размер файла, формат

**Критерии готовности:**
- Загрузка файла и URL работают
- Лекция создаётся в БД и начинает обрабатываться

---

### Задача 7.5 — Страница Lecture (главная страница лекции)
**Deliverables:**
- `frontend/src/pages/Lecture.tsx`:
  - Сплит-вью: левая часть (конспект) + правая часть (граф)
  - ProgressBar (подписан на WS) отображает процент обработки
  - После готовности показывает конспект и граф
- Компоненты:
  - `SummaryView.tsx` — список блоков конспекта с типами, иконка обогащения (галочка)
  - `EntityGraph.tsx` — интерактивный граф Vis.js (zoom, drag, клик → подсветка)
  - `ProgressBar.tsx` — прогрессбар, подписан на `WS ws://localhost:8000/ws/{lecture_id}`

**Критерии готовности:**
- При загрузке лекции показывается progressbar
- После готовности отображается конспект и граф
- Граф интерактивен (zoom, drag)

---

### Задача 7.6 — Компонент SummaryView (конспект)
**Deliverables:**
- `frontend/src/components/SummaryView.tsx`:
  - Список блоков с заголовками и текстом
  - Подсветка по типу (мысль, определение, дата, вывод) через цвета/иконки
  - Отображение таймкода (если есть) с ссылкой на видеоплеер
  - Кнопка обогащения (галочка) → POST `/lectures/{id}/summary/enrich`
  - При клике на сущность в тексте → подсветка в графе
- Обогащение: добавляет новые блоки с флагом `enriched: true`, отличный стиль (например, пунктирная рамка)

**Критерии готовности:**
- Блоки отображаются корректно
- Обогащение добавляет новые блоки

---

### Задача 7.7 — Компонент EntityGraph (интерактивный граф)
**Deliverables:**
- `frontend/src/components/EntityGraph.tsx` с использованием **vis-network** (или react-vis-graph):
  - Отображение узлов и рёбер
  - Интерактивность: zoom, drag, клик на узел
  - При клике на узел:
    - Подсветка узла
    - Вывод всех упоминаний сущности в конспекте/транскрипции (боковая панель или всплывающее окно)
    - Показ таймкодов
  - Кнопка обогащения (галочка) → POST `/lectures/{id}/graph/enrich`
  - Экспорт (иконка скачивания) → скачивание PNG или JSON
  - Фильтр узлов по типу (кнопки toggle для типов)

**Критерии готовности:**
- Граф рендерится и интерактивен
- Клик на узел показывает упоминания
- Обогащение добавляет новые узлы

---

### Задача 7.8 — Страницы Favourites и History
**Deliverables:**
- `frontend/src/pages/Favourites.tsx`:
  - Список избранных лекций (таблица или карточки)
  - Кнопка "Открыть" → `/lecture/{id}`
  - Кнопка "Удалить из избранного"
  - Пусто, если нет избранных
- `frontend/src/pages/History.tsx`:
  - Список просмотренных лекций с датой и временем
  - Сортировка по дате (новые сверху)
  - Кнопка "Открыть"
  - Опционально: очистка истории

**Критерии готовности:**
- Списки загружаются через API
- Функциональность работает (удаление, открытие)

---

## ФАЗА 8: Admin-панель (React)

### Задача 8.1 — Страница Admin Dashboard
**Deliverables:**
- `frontend/src/pages/admin/AdminDashboard.tsx`:
  - Главная страница админ-панели с навигацией (боковое меню или вкладки)
  - Ссылки на: Users, Statistics, Database Stats
  - Сводка: кол-во пользователей, лекций, размер хранилища, топ-5 сущностей
- Защита: все admin страницы доступны только если `user.is_admin === true` (проверка на клиенте + на сервере)

**Критерии готовности:**
- Admin может зайти на админ-панель
- Обычный пользователь не может

---

### Задача 8.2 — Admin: Управление пользователями
**Deliverables:**
- `frontend/src/pages/admin/UsersPage.tsx`:
  - Таблица пользователей: username, email, статус (active/inactive), дата создания, действия
  - Форма создания нового пользователя (модальное окно): username, email, пароль (или генерация)
  - Кнопки: активировать/деактивировать, удалить, выдать/пополнить токены
  - Поиск по username/email
- `frontend/src/components/CreateUserModal.tsx`
- `frontend/src/components/TokenModal.tsx` — выдача токенов

**Критерии готовности:**
- Таблица отображает всех пользователей
- Создание и деактивация работают

---

### Задача 8.3 — Admin: Статистика пользователей
**Deliverables:**
- `frontend/src/pages/admin/UserStatsPage.tsx`:
  - DateRangePicker для выбора интервала
  - AreaChart (Recharts) — график новых регистраций за период
  - Таблица: дата, кол-во новых пользователей
  - API: `GET /admin/stats/users?start_date=...&end_date=...`

**Критерии готовности:**
- График отображается корректно
- Данные соответствуют БД

---

### Задача 8.4 — Admin: Статистика посещений
**Deliverables:**
- `frontend/src/pages/admin/VisitStatsPage.tsx`:
  - DateRangePicker
  - ToggleGroup: "Все пользователи" / "Конкретный пользователь" (select)
  - BarChart (Recharts) — посещения по датам
  - Таблица: лекция, кол-во посещений, последнее посещение
  - API: `GET /admin/stats/visits?start_date=...&end_date=...&user_id=...`

**Критерии готовности:**
- График и таблица работают
- Фильтр по пользователю работает

---

### Задача 8.5 — Admin: Статистика БД
**Deliverables:**
- `frontend/src/pages/admin/DatabaseStatsPage.tsx`:
  - Карточки (KPI cards): кол-во лекций, пользователей, размер файлов, топ-10 сущностей
  - Таблица лекций с сортировкой: название, пользователь, размер, статус
  - API: `GET /admin/stats/db`

**Критерии готовности:**
- Все метрики отображаются
- Таблица отсортирована

---

## ФАЗА 9: Telegram-бот (aiogram 3)

### Задача 9.1 — Инициализация бота
**Deliverables:**
- `telegram_bot/bot.py` — главная точка входа
- `telegram_bot/config.py` — конфиг (TELEGRAM_BOT_TOKEN, API_BASE_URL)
- Настройка polling или webhook
- `telegram_bot/handlers/__init__.py` — регистрация handlers

**Критерии готовности:**
- Бот запускается и отвечает на сообщения
- Логирование настроено

---

### Задача 9.2 — Auth в боте и интеграция с API
**Deliverables:**
- `telegram_bot/auth.py`:
  - При первом `/start` — просьба email + пароль (или одноразовый токен от админа)
  - Запрос на API `/auth/login`, получение JWT
  - Сохранение JWT в БД (таблица `TelegramUser`: telegram_id, user_id, jwt_token, username)
  -验证при каждом запросе, refresh токена если истёк
- `telegram_bot/db.py` — модели SQLAlchemy для `TelegramUser`

**Критерии готовности:**
- Пользователь может авторизоваться через бот
- JWT используется в запросах к API

---

### Задача 9.3 — Main menu и handlers
**Deliverables:**
- `telegram_bot/handlers/menu.py`:
  - `/start` — главное меню с ReplyKeyboard кнопками: "📤 Загрузить", "📖 История", "⭐ Избранное", "💰 Мой баланс", "❓ Помощь"
  - `/help` — справка по доступным командам
- InlineKeyboards для действий (навигация по меню)

**Критерии готовности:**
- Кнопки работают и переводят на соответствующие handlers

---

### Задача 9.4 — Загрузка видео в боте
**Deliverables:**
- `telegram_bot/handlers/upload.py`:
  - Обработка файла (video, document) или URL (сообщение со ссылкой)
  - Инлайн-кнопки для выбора режима: "⚡ Мгновенно" vs "⏱️ По мере просмотра"
  - Отправка на API `/lectures`, получение lecture_id
  - Сообщение: "Обрабатывается... ⏳" с обновлением прогресса (через запросы `GET /lectures/{id}/progress` каждые 2 сек)
  - После готовности: "Готово! ✅" + инлайн-кнопки "Конспект", "Граф", "Сохранить", "В избранное"

**Критерии готовности:**
- Загрузка файла и URL работают
- Прогресс обновляется
- Действия после готовности работают

---

### Задача 9.5 — Просмотр конспекта в боте
**Deliverables:**
- `telegram_bot/handlers/summary.py`:
  - Обработка кнопки "Конспект"
  - Получение конспекта через API `GET /lectures/{id}/summary`
  - Разбивка на сообщения (Telegram limit ~4096 символов)
  - Форматирование (markdown): заголовки, жирный шрифт для ключевых терминов
  - Инлайн-кнопка "Скачать PDF" → `GET /lectures/{id}/export?format=pdf` (отправка файла)

**Критерии готовности:**
- Конспект отправляется несколькими сообщениями
- PDF скачивается

---

### Задача 9.6 — Просмотр графа в боте
**Deliverables:**
- `telegram_bot/handlers/graph.py`:
  - Обработка кнопки "Граф"
  - Получение графа через API `GET /lectures/{id}/graph/export?format=png`
  - Отправка PNG как photo с caption (граф сущностей, кол-во узлов/рёбер)
  - Опционально: кнопка "JSON" → отправка JSON файла

**Критерии готовности:**
- PNG граф отправляется
- JSON опционально отправляется

---

### Задача 9.7 — История и Избранное в боте
**Deliverables:**
- `telegram_bot/handlers/history.py`:
  - Кнопка "История"
  - Получение списка через API `GET /history`
  - Вывод последних 10 лекций (пагинация через кнопки "Ещё" / "Назад")
  - Каждая лекция как inline-кнопка (название) → открытие меню с "Конспект", "Граф", "Удалить из истории"
- `telegram_bot/handlers/favourites.py`:
  - Аналогично для избранного
  - Кнопка "В избранное" / "Удалить из избранного" переключает статус

**Критерии готовности:**
- Списки загружаются и отображаются
- Пагинация работает
- Добавление/удаление работает

---

### Задача 9.8 — Баланс токенов в боте
**Deliverables:**
- `telegram_bot/handlers/balance.py`:
  - Кнопка "Баланс"
  - Запрос `GET /tokens/balance`
  - Вывод: "Ваш баланс: 500 токенов 💰"
  - Опционально: таблица транзакций (последние 5)

**Критерии готовности:**
- Баланс отображается корректно

---

## ФАЗА 10: Точность работы (Accuracy Evaluation)

### Задача 10.1 — Сервис оценки точности
**Deliverables:**
- `backend/app/services/accuracy_service.py`:
  - Функция `evaluate_accuracy(test_texts: list[dict]) -> dict`
  - Структура test_texts: `[{text: str, gold_entities: [{label, type, start, end}]}, ...]`
  - Запуск extraction на каждом тексте
  - Расчёт Precision, Recall, F1 по совпадению сущностей (token-level или exact match)
  - Возврат: `{precision, recall, f1, details: [{text, predicted_entities, gold_entities, errors}]}`
- `backend/app/models/accuracy_result.py`:
  ```python
  class AccuracyResult:
    - id: UUID
    - evaluated_at: datetime
    - num_texts: int
    - precision: float
    - recall: float
    - f1: float
    - details: JSONB (для хранения полных результатов)
  ```

**Критерии готовности:**
- Метрики вычисляются корректно
- Результаты сохраняются в БД

---

### Задача 10.2 — Admin API для оценки
**Deliverables:**
- `backend/app/api/admin/accuracy.py`:
  - `POST /admin/accuracy-eval` — принять list[dict] с тестовыми текстами, запустить evaluation
  - `GET /admin/accuracy-results` — получить историю оценок
  - Запуск как Celery task для долгих операций

**Критерии готовности:**
- API работает
- Результаты сохраняются

---

### Задача 10.3 — Страница Results в Admin
**Deliverables:**
- `frontend/src/pages/admin/AccuracyPage.tsx`:
  - Форма загрузки тестовых данных (JSON, CSV, textarea)
  - Кнопка "Запустить оценку"
  - Таблица результатов оценок: дата, P/R/F1, количество текстов
  - Клик на результат → детали (таблица с ошибками, confusion matrix)
  - График P/R/F1 по времени (если несколько оценок)

**Критерии готовности:**
- Загрузка и оценка работают
- Результаты отображаются

---

## ФАЗА 11: Токены и монетизация

### Задача 11.1 — Система токенов (backend)
**Deliverables:**
- `backend/app/core/config.py` добавить стоимость операций:
  ```python
  COST_TRANSCRIBE = 50
  COST_SUMMARIZE = 30
  COST_EXTRACT_ENTITIES = 40
  COST_ENRICH = 25
  ```
- `backend/app/services/token_service.py`:
  - `check_balance(user_id: UUID, required: int) -> bool` — проверка баланса
  - `deduct_tokens(user_id: UUID, amount: int, reason: str) -> TokenTransaction`
  - `add_tokens(user_id: UUID, amount: int, reason: str) -> TokenTransaction`
- Перед каждой Celery task — проверка баланса; после выполнения — списание

**Критерии готовности:**
- Баланс проверяется и списывается корректно
- Транзакции записываются в БД

---

### Задача 11.2 — API для управления токенами
**Deliverables:**
- `backend/app/api/tokens.py`:
  - `GET /tokens/balance` — текущий баланс пользователя
  - `GET /tokens/history` — история транзакций (пагинация)
- `backend/app/api/admin/tokens.py`:
  - `POST /admin/tokens/{user_id}` — выдача токенов админом (с reason)

**Критерии готовности:**
- Endpoints работают

---

### Задача 11.3 — UI для баланса токенов
**Deliverables:**
- `frontend/src/components/TokenBadge.tsx` — отображение баланса в хедере (сверху справа)
- `frontend/src/pages/TokensPage.tsx` — страница с историей и информацией о стоимости операций
- Таблица транзакций с датой, действием, суммой, остатком

**Критерии готовности:**
- Баланс отображается в интерфейсе
- История работает

---

## ФАЗА 12: Многопользовательский режим и безопасность

### Задача 12.1 — Настройка Celery для мультиворкера
**Deliverables:**
- `docker-compose.yml` обновить:
  - `celery_worker` сервис с `command: celery -A backend.celery_app worker --loglevel=info --concurrency=4`
  - Опционально: несколько worker'ов (celery_worker_1, celery_worker_2)
- Тестирование параллельной загрузки нескольких видео

**Критерии готовности:**
- Несколько лекций обрабатываются одновременно
- Нет конфликтов в БД

---

### Задача 12.2 — Настройка PostgreSQL для параллельного доступа
**Deliverables:**
- `docker-compose.yml`: `postgres` с переменными окружения для оптимизации
- `backend/app/core/database.py`:
  - Пул соединений: `pool_size=20, max_overflow=40`
  - `echo=False` для production
- Тестирование одновременного доступа (нагрузочное тестирование)

**Критерии готовности:**
- БД обрабатывает параллельные запросы без deadlock'ов

---

### Задача 12.3 — Rate Limiting
**Deliverables:**
- Интеграция `slowapi`:
  - `backend/app/core/limiter.py` — инициализация
  - Применение к роутерам: `/lectures` (10 req/min), `/auth/login` (5 req/min), разное для разных endpoints
  - Учёт user_id в лимитах (не IP)
- Обработка 429 ошибок на клиенте

**Критерии готовности:**
- Лимиты работают
- Клиент получает 429 при превышении

---

### Задача 12.4 — Изоляция данных
**Deliverables:**
- Middleware для проверки ownership при каждом запросе:
  - `GET /lectures/{id}` — проверить, что это лекция пользователя
  - Аналогично для summary, graph, export
  - При нарушении → 403 Forbidden
- Тестирование: попытка доступа к чужим данным должна быть заблокирована

**Критерии готовности:**
- Пользователь не может доступиться к чужим лекциям

---

### Задача 12.5 — Безопасность загрузки файлов
**Deliverables:**
- `backend/app/services/file_service.py`:
  - Ограничение размера: MAX_FILE_SIZE (например, 500 MB)
  - Валидация MIME-типа: проверка content-type и расширения
  - Сканирование вирусов через `pyclamav` (опционально)
  - Сохранение под UUID, не исходным именем (защита от traversal атак)
  - Удаление файлов при удалении лекции

**Критерии готовности:**
- Загрузка больших файлов блокируется
- Неподдерживаемые форматы отклоняются
- Файлы сохраняются безопасно

---

## ФАЗА 13: Дополнительные требования

### Задача 13.1 — Progress Indicator (опционально, но рекомендуется)
**Deliverables:**
- Уже реализовано в Задаче 5.7 (Celery Chain обновляет прогресс)
- Дополнение: SSE (Server-Sent Events) как альтернатива WebSocket (если нужна только однонаправленная коммуникация)
- `backend/app/api/lectures.py` добавить:
  - `GET /lectures/{id}/progress?stream=true` — SSE endpoint
- Frontend подписан на SSE или WS

**Критерии готовности:**
- Progressbar обновляется в реальном времени

---

### Задача 13.2 — Локальные LLM модели (опционально)
**Deliverables:**
- Поддержка Ollama через LiteLLM:
  - Конфиг `backend/app/core/config.py`: `OLLAMA_BASE_URL` (по умолчанию `http://localhost:11434`)
  - Если `LLM_PROVIDER == "ollama"` → использовать Ollama вместо OpenAI
- Docker-compose.yml опционально: `ollama` сервис
- Документация по установке локального Ollama

**Критерии готовности:**
- Система работает с локальной моделью

---

### Задача 13.3 — Кеширование результатов (опционально)
**Deliverables:**
- Redis для кеширования:
  - Кеш summary/graph на день (зависит от контента)
  - Кеш списков лекций/истории на 5 минут
  - Инвалидация при изменении данных
- Использование `redis.Redis` и TTL

**Критерии готовности:**
- Повторные запросы возвращаются из кеша быстрее
- Данные актуальны

---

### Задача 13.4 — Инструментирование и логирование
**Deliverables:**
- `backend/app/core/logging.py` — конфиг логирования
- Логирование: INFO для основных событий, ERROR для ошибок, DEBUG для разработки
- Интеграция с Sentry (опционально) для отслеживания ошибок в production
- Логирование в file и stdout

**Критерии готовности:**
- Логи пишутся в файл и консоль
- Ошибки содержат traceback

---

## Раскладка по этапам разработки

| Этап | Фазы | Время (дни) |
|---|---|---|
| **Backend infrastructure** | 1, 2 | 5–7 |
| **Authentication** | 3 | 3–4 |
| **API core** | 4, 6 | 4–5 |
| **Video processing** | 5 | 7–10 |
| **LLM integration** | 5 (доп) | 3–5 |
| **Frontend setup** | 7.1 | 2 |
| **Main UI pages** | 7.2–7.8 | 8–10 |
| **Admin panel** | 8 | 4–5 |
| **Telegram bot** | 9 | 4–6 |
| **Polish & Testing** | 10–13 | 5–7 |
| **Total (solo)** | | 45–60 дней |
| **Total (team of 3–4)** | | 15–20 дней |