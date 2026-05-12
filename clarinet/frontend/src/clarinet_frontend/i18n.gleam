// Compile-time i18n: exhaustive case match guarantees complete translations.
// Adding a Key variant without both En/Ru arms is a compile error.

pub type Locale {
  En
  Ru
}

pub type Key {
  // --- Navigation ---
  NavRecords
  NavStudies
  NavPatients
  NavRecordTypes
  NavReports
  NavWorkflow
  NavAdmin

  // --- Common buttons ---
  BtnLogout
  BtnView
  BtnEdit
  BtnChange
  BtnCancel
  BtnDelete
  BtnBack
  BtnFail
  BtnRestart
  BtnFill
  BtnClearFilters
  BtnLogin
  BtnNewPatient
  BtnCreateRecord

  // --- Common table headers ---
  ThId
  ThName
  ThStatus
  ThActions
  ThType
  ThPatient
  ThDate
  ThDescription
  ThModality
  ThStudy
  ThStudySeries
  ThUser
  ThStudies
  ThSeries
  ThAnonymized
  ThRecordType
  ThAnonId
  ThAnonName
  ThAnonUid
  ThLabel
  ThLevel
  ThRole
  ThMinMaxUsers
  ThTotalRecords
  ThUniqueUsers
  ThNumber
  ThStudyUid
  ThSeriesUid
  ThAssignedUser

  // --- Common labels ---
  LblYes
  LblNo
  LblLoading

  // --- Status badges ---
  StatusBlocked
  StatusPending
  StatusInProgress
  StatusCompleted
  StatusFailed
  StatusPaused

  // --- Filters ---
  FilterAllStatuses
  FilterAllTypes
  FilterAllPatients
  FilterAllUsers

  // --- Layout ---
  FooterCopyright(project_name: String, project_description: String)

  // --- Login ---
  LoginEmail
  LoginPassword
  LoginEmailPlaceholder
  LoginPasswordPlaceholder
  LoginSubmit
  LoginSubmitting
  LoginNoAccount
  LoginRegisterLink
  LoginFailed

  // --- Register ---
  RegisterTitle
  RegisterFor(project: String)
  RegisterEmailPlaceholder
  RegisterEmailHelp
  RegisterPasswordPlaceholder
  RegisterPasswordHelp
  RegisterConfirmPassword
  RegisterConfirmPlaceholder
  RegisterSubmit
  RegisterSubmitting
  RegisterHasAccount
  RegisterLoginLink
  RegisterPasswordMismatch
  RegisterPasswordTooShort
  RegisterSuccess(project: String)
  RegisterInvalidData
  RegisterDuplicate

  // --- Home / Dashboard ---
  HomeDashboard
  HomeWelcome(email: String)
  HomeWelcomeTo(project: String)
  HomeLoginPrompt
  HomeOverview
  HomeStudies
  HomeRecords
  HomeMyRecords
  HomeViewAll
  HomeRecentStudies
  HomeNoRecentStudies

  // --- Records ---
  RecordsAllTitle
  RecordsTitle
  RecordsNoFound
  RecordsMsgRestarted
  RecordsMsgRestartFailed
  RecordsNewTitle

  // --- Patients ---
  PatientsTitle
  PatientsNoFound
  PatientsNewTitle
  PatientsMsgCreated
  PatientsMsgCreateFailed
  PatientsMsgConflict(patient_id: String, patient_name: String)
  PatientPrefix(id: String)
  PatientBackToPatients
  PatientBtnDelete
  PatientInfo
  PatientLblId
  PatientLblName
  PatientLblAnonId
  PatientLblAnonName
  PatientBtnAnonymize
  PatientStudies
  PatientNoStudies
  PatientNoRecords
  PatientNoRecordsMatch
  PatientMsgAnonymized
  PatientMsgAnonymizeFailed
  PatientMsgDeleted
  PatientMsgDeleteFailed
  PatientMsgLoadFailed
  PatientPacsTitle
  PatientPacsSearching
  PatientPacsBtnSearch
  PatientPacsBtnClear
  PatientPacsSearchingMsg
  PatientPacsAlreadyAdded
  PatientPacsImporting
  PatientPacsBtnAdd
  PatientPacsShowSeries
  PatientPacsNoDescription
  PatientPacsImages(count: String)
  PatientMsgStudyImported
  PatientMsgImportFailed
  PatientMsgPacsSearchFailed
  PatientLoading(id: String)

  // --- Studies ---
  StudiesTitle
  StudiesNoFound
  StudyPrefix(uid: String)
  StudyBackToStudies
  StudyBtnDelete
  StudyInfo
  StudyLblUid
  StudyLblDate
  StudyLblAnonUid
  StudyLblPatientId
  StudyPatient
  StudySeries
  StudyNoSeries
  StudyRecords
  StudyNoRecords
  StudyMsgDeleted
  StudyMsgDeleteFailed
  StudyMsgLoadFailed
  StudyLoading(uid: String)

  // --- Series ---
  SeriesPrefix(uid: String)
  SeriesBackToStudy
  SeriesInfo
  SeriesLblUid
  SeriesLblDescription
  SeriesLblNumber
  SeriesLblAnonUid
  SeriesLblWorkingFolder
  SeriesLblStudyUid
  SeriesParentStudy
  SeriesRecords
  SeriesNoRecords
  SeriesMsgLoadFailed
  SeriesLoading(uid: String)

  // --- Record Types ---
  RecordTypesNoData
  RecordTypesNoFound
  RecordTypePrefix(name: String)
  RecordTypeBackToList
  RecordTypeBtnEdit
  RecordTypeInfo
  RecordTypeLblName
  RecordTypeLblLabel
  RecordTypeLblDescription
  RecordTypeLblLevel
  RecordTypeLblRole
  RecordTypeLblMinRecords
  RecordTypeLblMaxRecords
  RecordTypeRecords
  RecordTypeNoRecords
  RecordTypeEditPrefix(name: String)
  RecordTypeEditBack
  RecordTypeMsgUpdated
  RecordTypeMsgUpdateFailed
  RecordTypeMsgLoadFailed
  RecordTypeLoading(name: String)

  // --- Admin ---
  AdminDashboardTitle
  AdminSystemOverview
  AdminStatStudies
  AdminStatRecords
  AdminStatUsers
  AdminStatPatients
  AdminRecordsByStatus
  AdminRoleMatrix
  AdminRoleMatrixLoading
  AdminNoRoles
  AdminBadge
  AdminRecords
  AdminNoRecords
  AdminSelectUser
  AdminSelectStatus
  AdminMsgUserAssigned
  AdminMsgUserAssignFailed
  AdminMsgStatusUpdated
  AdminMsgStatusUpdateFailed
  AdminMsgRoleUpdated
  AdminMsgRoleUpdateFailed
  AdminMsgLoadFailed
  AdminMsgRoleMatrixFailed

  // --- Execute / Slicer ---
  ExecTitle
  ExecDefaultDesc
  ExecBtnOpenSlicer
  ExecBtnComplete
  ExecBtnResubmit
  ExecSlicerTitle
  ExecSlicerConnected
  ExecSlicerUnreachable
  ExecSlicerChecking
  ExecNoFormRequired
  ExecRecordCompleted
  ExecNoFormDefined
  ExecNoDataSubmitted
  ExecNoDataYet
  ExecRecordData
  ExecLblPatient
  ExecLblStudy
  ExecLblSeries
  ExecLblCreated
  ExecLblAssigned
  ExecRecordTypeNotFound
  ExecBackToRecords
  ExecMsgSlicerOpened
  ExecMsgSlicerFailed
  ExecMsgValidationDone
  ExecMsgValidationFailed
  ExecMsgDataSubmitted
  ExecMsgCompleted
  ExecMsgResubmitted
  ExecMsgDataFailed
  ExecMsgCompleteFailed
  ExecMsgResubmitFailed
  ExecMsgRestartFailed
  ExecSlicerNotReachable
  ExecSlicerError(msg: String)
  ExecNetworkError(msg: String)
  ExecImages(count: String)

  // --- Modals (main.gleam) ---
  ModalMarkAsFailed
  ModalReason
  ModalReasonPlaceholder
  ModalDeletePatientTitle
  ModalDeletePatientWarning(id: String)
  ModalDeleteStudyTitle
  ModalDeleteStudyWarning(uid: String)
  ModalConfirm
  ModalAreYouSure
  Page404
  PageNotFound
  MsgSessionExpired
  MsgFailRecordFailed

  // --- Forms ---
  FormPatientInfo
  FormPatientId
  FormPatientIdPlaceholder
  FormPatientName
  FormPatientNamePlaceholder
  FormBtnCreatePatient
  FormRecordInfo
  FormRecordType
  FormSelectRecordType
  FormPatient
  FormSelectPatient
  FormStudy
  FormSelectStudy
  FormSeries
  FormSelectSeries
  FormAssignUser
  FormNoUserUnassigned
  FormParentRecord
  FormNoParentRecord
  FormContextInfo
  FormContextPlaceholder
  FormBtnCreateRecord
}

pub fn translate(locale: Locale, key: Key) -> String {
  case locale, key {
    // --- Navigation ---
    En, NavRecords -> "Records"
    Ru, NavRecords -> "Записи"
    En, NavStudies -> "Studies"
    Ru, NavStudies -> "Исследования"
    En, NavPatients -> "Patients"
    Ru, NavPatients -> "Пациенты"
    En, NavRecordTypes -> "Record Types"
    Ru, NavRecordTypes -> "Типы записей"
    En, NavReports -> "Reports"
    Ru, NavReports -> "Отчёты"
    En, NavWorkflow -> "Workflow"
    Ru, NavWorkflow -> "Граф процессов"
    En, NavAdmin -> "Admin"
    Ru, NavAdmin -> "Админ"

    // --- Common buttons ---
    En, BtnLogout -> "Logout"
    Ru, BtnLogout -> "Выход"
    En, BtnView -> "View"
    Ru, BtnView -> "Просмотр"
    En, BtnEdit -> "Edit"
    Ru, BtnEdit -> "Изменить"
    En, BtnChange -> "Change"
    Ru, BtnChange -> "Изменить"
    En, BtnCancel -> "Cancel"
    Ru, BtnCancel -> "Отмена"
    En, BtnDelete -> "Delete"
    Ru, BtnDelete -> "Удалить"
    En, BtnBack -> "Back"
    Ru, BtnBack -> "Назад"
    En, BtnFail -> "Fail"
    Ru, BtnFail -> "Ошибка"
    En, BtnRestart -> "Restart"
    Ru, BtnRestart -> "Перезапуск"
    En, BtnFill -> "Fill"
    Ru, BtnFill -> "Заполнить"
    En, BtnClearFilters -> "Clear Filters"
    Ru, BtnClearFilters -> "Сбросить фильтры"
    En, BtnLogin -> "Login"
    Ru, BtnLogin -> "Войти"
    En, BtnNewPatient -> "New Patient"
    Ru, BtnNewPatient -> "Новый пациент"
    En, BtnCreateRecord -> "Create Record"
    Ru, BtnCreateRecord -> "Создать запись"

    // --- Common table headers ---
    En, ThId -> "ID"
    Ru, ThId -> "ID"
    En, ThName -> "Name"
    Ru, ThName -> "Имя"
    En, ThStatus -> "Status"
    Ru, ThStatus -> "Статус"
    En, ThActions -> "Actions"
    Ru, ThActions -> "Действия"
    En, ThType -> "Type"
    Ru, ThType -> "Тип"
    En, ThPatient -> "Patient"
    Ru, ThPatient -> "Пациент"
    En, ThDate -> "Date"
    Ru, ThDate -> "Дата"
    En, ThDescription -> "Description"
    Ru, ThDescription -> "Описание"
    En, ThModality -> "Modality"
    Ru, ThModality -> "Модальность"
    En, ThStudy -> "Study"
    Ru, ThStudy -> "Исследование"
    En, ThStudySeries -> "Study / Series"
    Ru, ThStudySeries -> "Исследование / Серия"
    En, ThUser -> "User"
    Ru, ThUser -> "Пользователь"
    En, ThStudies -> "Studies"
    Ru, ThStudies -> "Исследования"
    En, ThSeries -> "Series"
    Ru, ThSeries -> "Серии"
    En, ThAnonymized -> "Anonymized"
    Ru, ThAnonymized -> "Анонимизировано"
    En, ThRecordType -> "Record Type"
    Ru, ThRecordType -> "Тип записи"
    En, ThAnonId -> "Anon ID"
    Ru, ThAnonId -> "Анон. ID"
    En, ThAnonName -> "Anon Name"
    Ru, ThAnonName -> "Анон. имя"
    En, ThAnonUid -> "Anon UID"
    Ru, ThAnonUid -> "Анон. UID"
    En, ThLabel -> "Label"
    Ru, ThLabel -> "Метка"
    En, ThLevel -> "Level"
    Ru, ThLevel -> "Уровень"
    En, ThRole -> "Role"
    Ru, ThRole -> "Роль"
    En, ThMinMaxUsers -> "Min/Max Users"
    Ru, ThMinMaxUsers -> "Мин/Макс"
    En, ThTotalRecords -> "Total Records"
    Ru, ThTotalRecords -> "Всего записей"
    En, ThUniqueUsers -> "Unique Users"
    Ru, ThUniqueUsers -> "Уник. пользователей"
    En, ThNumber -> "Number"
    Ru, ThNumber -> "Номер"
    En, ThStudyUid -> "Study UID"
    Ru, ThStudyUid -> "UID исследования"
    En, ThSeriesUid -> "Series UID"
    Ru, ThSeriesUid -> "UID серии"
    En, ThAssignedUser -> "Assigned User"
    Ru, ThAssignedUser -> "Назначенный"

    // --- Common labels ---
    En, LblYes -> "Yes"
    Ru, LblYes -> "Да"
    En, LblNo -> "No"
    Ru, LblNo -> "Нет"
    En, LblLoading -> "Loading..."
    Ru, LblLoading -> "Загрузка..."

    // --- Status badges ---
    En, StatusBlocked -> "Blocked"
    Ru, StatusBlocked -> "Заблокирована"
    En, StatusPending -> "Pending"
    Ru, StatusPending -> "Ожидание"
    En, StatusInProgress -> "In Progress"
    Ru, StatusInProgress -> "В работе"
    En, StatusCompleted -> "Completed"
    Ru, StatusCompleted -> "Завершена"
    En, StatusFailed -> "Failed"
    Ru, StatusFailed -> "Ошибка"
    En, StatusPaused -> "Paused"
    Ru, StatusPaused -> "Приостановлена"

    // --- Filters ---
    En, FilterAllStatuses -> "All Statuses"
    Ru, FilterAllStatuses -> "Все статусы"
    En, FilterAllTypes -> "All Types"
    Ru, FilterAllTypes -> "Все типы"
    En, FilterAllPatients -> "All Patients"
    Ru, FilterAllPatients -> "Все пациенты"
    En, FilterAllUsers -> "All Users"
    Ru, FilterAllUsers -> "Все пользователи"

    // --- Layout ---
    En, FooterCopyright(name, desc) -> "© 2024 " <> name <> " " <> desc
    Ru, FooterCopyright(name, desc) -> "© 2024 " <> name <> " " <> desc

    // --- Login ---
    En, LoginEmail -> "Email"
    Ru, LoginEmail -> "Email"
    En, LoginPassword -> "Password"
    Ru, LoginPassword -> "Пароль"
    En, LoginEmailPlaceholder -> "Enter your email"
    Ru, LoginEmailPlaceholder -> "Введите email"
    En, LoginPasswordPlaceholder -> "Enter password"
    Ru, LoginPasswordPlaceholder -> "Введите пароль"
    En, LoginSubmit -> "Login"
    Ru, LoginSubmit -> "Войти"
    En, LoginSubmitting -> "Logging in..."
    Ru, LoginSubmitting -> "Вход..."
    En, LoginNoAccount -> "Don't have an account? "
    Ru, LoginNoAccount -> "Нет аккаунта? "
    En, LoginRegisterLink -> "Register here"
    Ru, LoginRegisterLink -> "Зарегистрироваться"
    En, LoginFailed -> "Login failed. Please try again."
    Ru, LoginFailed -> "Ошибка входа. Попробуйте ещё раз."

    // --- Register ---
    En, RegisterTitle -> "Create Account"
    Ru, RegisterTitle -> "Создать аккаунт"
    En, RegisterFor(project) -> "Register for " <> project
    Ru, RegisterFor(project) -> "Регистрация в " <> project
    En, RegisterEmailPlaceholder -> "your.email@example.com"
    Ru, RegisterEmailPlaceholder -> "your.email@example.com"
    En, RegisterEmailHelp -> "This will be your unique identifier for login"
    Ru, RegisterEmailHelp -> "Будет использоваться для входа"
    En, RegisterPasswordPlaceholder -> "Enter a strong password"
    Ru, RegisterPasswordPlaceholder -> "Введите надёжный пароль"
    En, RegisterPasswordHelp -> "Minimum 8 characters"
    Ru, RegisterPasswordHelp -> "Минимум 8 символов"
    En, RegisterConfirmPassword -> "Confirm Password"
    Ru, RegisterConfirmPassword -> "Подтверждение пароля"
    En, RegisterConfirmPlaceholder -> "Re-enter your password"
    Ru, RegisterConfirmPlaceholder -> "Повторите пароль"
    En, RegisterSubmit -> "Register"
    Ru, RegisterSubmit -> "Зарегистрироваться"
    En, RegisterSubmitting -> "Creating account..."
    Ru, RegisterSubmitting -> "Создание аккаунта..."
    En, RegisterHasAccount -> "Already have an account? "
    Ru, RegisterHasAccount -> "Уже есть аккаунт? "
    En, RegisterLoginLink -> "Login here"
    Ru, RegisterLoginLink -> "Войти"
    En, RegisterPasswordMismatch -> "Passwords do not match"
    Ru, RegisterPasswordMismatch -> "Пароли не совпадают"
    En, RegisterPasswordTooShort -> "Password must be at least 8 characters"
    Ru, RegisterPasswordTooShort -> "Пароль должен быть не менее 8 символов"
    En, RegisterSuccess(project) -> "Registration successful! Welcome to " <> project
    Ru, RegisterSuccess(project) -> "Регистрация успешна! Добро пожаловать в " <> project
    En, RegisterInvalidData -> "Invalid registration data. Please check your inputs."
    Ru, RegisterInvalidData -> "Неверные данные. Проверьте введённые поля."
    En, RegisterDuplicate -> "Username or email already exists."
    Ru, RegisterDuplicate -> "Пользователь с таким email уже существует."

    // --- Home / Dashboard ---
    En, HomeDashboard -> "Dashboard"
    Ru, HomeDashboard -> "Панель управления"
    En, HomeWelcome(email) -> "Welcome back, " <> email <> "!"
    Ru, HomeWelcome(email) -> "С возвращением, " <> email <> "!"
    En, HomeWelcomeTo(project) -> "Welcome to " <> project
    Ru, HomeWelcomeTo(project) -> "Добро пожаловать в " <> project
    En, HomeLoginPrompt -> "Please log in to access the dashboard."
    Ru, HomeLoginPrompt -> "Войдите, чтобы получить доступ к панели управления."
    En, HomeOverview -> "Overview"
    Ru, HomeOverview -> "Обзор"
    En, HomeStudies -> "Studies"
    Ru, HomeStudies -> "Исследования"
    En, HomeRecords -> "Records"
    Ru, HomeRecords -> "Записи"
    En, HomeMyRecords -> "My Records"
    Ru, HomeMyRecords -> "Мои записи"
    En, HomeViewAll -> "View all →"
    Ru, HomeViewAll -> "Показать все →"
    En, HomeRecentStudies -> "Recent Studies"
    Ru, HomeRecentStudies -> "Недавние исследования"
    En, HomeNoRecentStudies -> "No recent studies found."
    Ru, HomeNoRecentStudies -> "Нет недавних исследований."

    // --- Records ---
    En, RecordsAllTitle -> "All Records"
    Ru, RecordsAllTitle -> "Все записи"
    En, RecordsTitle -> "Records"
    Ru, RecordsTitle -> "Записи"
    En, RecordsNoFound -> "No records found."
    Ru, RecordsNoFound -> "Записи не найдены."
    En, RecordsMsgRestarted -> "Record restarted successfully"
    Ru, RecordsMsgRestarted -> "Запись успешно перезапущена"
    En, RecordsMsgRestartFailed -> "Failed to restart record"
    Ru, RecordsMsgRestartFailed -> "Не удалось перезапустить запись"
    En, RecordsNewTitle -> "New Record"
    Ru, RecordsNewTitle -> "Новая запись"

    // --- Patients ---
    En, PatientsTitle -> "Patients"
    Ru, PatientsTitle -> "Пациенты"
    En, PatientsNoFound -> "No patients found."
    Ru, PatientsNoFound -> "Пациенты не найдены."
    En, PatientsNewTitle -> "New Patient"
    Ru, PatientsNewTitle -> "Новый пациент"
    En, PatientsMsgCreated -> "Patient created successfully"
    Ru, PatientsMsgCreated -> "Пациент успешно создан"
    En, PatientsMsgCreateFailed -> "Failed to create patient"
    Ru, PatientsMsgCreateFailed -> "Не удалось создать пациента"
    En, PatientsMsgConflict(id, name) ->
      "Patient already exists. ID: " <> id <> ", Name: " <> name
    Ru, PatientsMsgConflict(id, name) ->
      "Такой пациент уже есть в базе. ID: " <> id <> ", ФИО: " <> name
    En, PatientPrefix(id) -> "Patient: " <> id
    Ru, PatientPrefix(id) -> "Пациент: " <> id
    En, PatientBackToPatients -> "Back to Patients"
    Ru, PatientBackToPatients -> "К списку пациентов"
    En, PatientBtnDelete -> "Delete Patient"
    Ru, PatientBtnDelete -> "Удалить пациента"
    En, PatientInfo -> "Patient Information"
    Ru, PatientInfo -> "Информация о пациенте"
    En, PatientLblId -> "ID:"
    Ru, PatientLblId -> "ID:"
    En, PatientLblName -> "Name:"
    Ru, PatientLblName -> "Имя:"
    En, PatientLblAnonId -> "Anonymous ID:"
    Ru, PatientLblAnonId -> "Анонимный ID:"
    En, PatientLblAnonName -> "Anonymous Name:"
    Ru, PatientLblAnonName -> "Анонимное имя:"
    En, PatientBtnAnonymize -> "Anonymize Patient"
    Ru, PatientBtnAnonymize -> "Анонимизировать"
    En, PatientStudies -> "Studies"
    Ru, PatientStudies -> "Исследования"
    En, PatientNoStudies -> "No studies found for this patient."
    Ru, PatientNoStudies -> "Исследования для этого пациента не найдены."
    En, PatientNoRecords -> "No records found for this patient."
    Ru, PatientNoRecords -> "Записи для этого пациента не найдены."
    En, PatientNoRecordsMatch -> "No records match the current filters."
    Ru, PatientNoRecordsMatch -> "Нет записей, соответствующих фильтрам."
    En, PatientMsgAnonymized -> "Patient anonymized successfully"
    Ru, PatientMsgAnonymized -> "Пациент успешно анонимизирован"
    En, PatientMsgAnonymizeFailed -> "Failed to anonymize patient"
    Ru, PatientMsgAnonymizeFailed -> "Не удалось анонимизировать пациента"
    En, PatientMsgDeleted -> "Patient deleted successfully"
    Ru, PatientMsgDeleted -> "Пациент успешно удалён"
    En, PatientMsgDeleteFailed -> "Failed to delete patient"
    Ru, PatientMsgDeleteFailed -> "Не удалось удалить пациента"
    En, PatientMsgLoadFailed -> "Failed to load patient"
    Ru, PatientMsgLoadFailed -> "Не удалось загрузить пациента"
    En, PatientPacsTitle -> "Add Study from PACS"
    Ru, PatientPacsTitle -> "Добавить исследование из PACS"
    En, PatientPacsSearching -> "Searching..."
    Ru, PatientPacsSearching -> "Поиск..."
    En, PatientPacsBtnSearch -> "Search PACS"
    Ru, PatientPacsBtnSearch -> "Поиск в PACS"
    En, PatientPacsBtnClear -> "Clear Results"
    Ru, PatientPacsBtnClear -> "Очистить"
    En, PatientPacsSearchingMsg -> "Searching PACS..."
    Ru, PatientPacsSearchingMsg -> "Поиск в PACS..."
    En, PatientPacsAlreadyAdded -> "Already added"
    Ru, PatientPacsAlreadyAdded -> "Уже добавлено"
    En, PatientPacsImporting -> "Importing..."
    Ru, PatientPacsImporting -> "Импорт..."
    En, PatientPacsBtnAdd -> "Add"
    Ru, PatientPacsBtnAdd -> "Добавить"
    En, PatientPacsShowSeries -> "Show series"
    Ru, PatientPacsShowSeries -> "Показать серии"
    En, PatientPacsNoDescription -> "No description"
    Ru, PatientPacsNoDescription -> "Без описания"
    En, PatientPacsImages(count) -> count <> " images"
    Ru, PatientPacsImages(count) -> count <> " изобр."
    En, PatientMsgStudyImported -> "Study imported from PACS successfully"
    Ru, PatientMsgStudyImported -> "Исследование успешно импортировано из PACS"
    En, PatientMsgImportFailed -> "Failed to import study from PACS"
    Ru, PatientMsgImportFailed -> "Не удалось импортировать исследование из PACS"
    En, PatientMsgPacsSearchFailed -> "Failed to search PACS"
    Ru, PatientMsgPacsSearchFailed -> "Не удалось выполнить поиск в PACS"
    En, PatientLoading(id) -> "Loading patient " <> id
    Ru, PatientLoading(id) -> "Загрузка пациента " <> id

    // --- Studies ---
    En, StudiesTitle -> "Studies"
    Ru, StudiesTitle -> "Исследования"
    En, StudiesNoFound -> "No studies found."
    Ru, StudiesNoFound -> "Исследования не найдены."
    En, StudyPrefix(uid) -> "Study: " <> uid
    Ru, StudyPrefix(uid) -> "Исследование: " <> uid
    En, StudyBackToStudies -> "Back to Studies"
    Ru, StudyBackToStudies -> "К списку исследований"
    En, StudyBtnDelete -> "Delete Study"
    Ru, StudyBtnDelete -> "Удалить исследование"
    En, StudyInfo -> "Study Information"
    Ru, StudyInfo -> "Информация об исследовании"
    En, StudyLblUid -> "Study UID:"
    Ru, StudyLblUid -> "UID исследования:"
    En, StudyLblDate -> "Date:"
    Ru, StudyLblDate -> "Дата:"
    En, StudyLblAnonUid -> "Anonymous UID:"
    Ru, StudyLblAnonUid -> "Анонимный UID:"
    En, StudyLblPatientId -> "Patient ID:"
    Ru, StudyLblPatientId -> "ID пациента:"
    En, StudyPatient -> "Patient"
    Ru, StudyPatient -> "Пациент"
    En, StudySeries -> "Series"
    Ru, StudySeries -> "Серии"
    En, StudyNoSeries -> "No series found for this study."
    Ru, StudyNoSeries -> "Серии для этого исследования не найдены."
    En, StudyRecords -> "Records"
    Ru, StudyRecords -> "Записи"
    En, StudyNoRecords -> "No records found for this study."
    Ru, StudyNoRecords -> "Записи для этого исследования не найдены."
    En, StudyMsgDeleted -> "Study deleted successfully"
    Ru, StudyMsgDeleted -> "Исследование успешно удалено"
    En, StudyMsgDeleteFailed -> "Failed to delete study"
    Ru, StudyMsgDeleteFailed -> "Не удалось удалить исследование"
    En, StudyMsgLoadFailed -> "Failed to load study"
    Ru, StudyMsgLoadFailed -> "Не удалось загрузить исследование"
    En, StudyLoading(uid) -> "Loading study " <> uid
    Ru, StudyLoading(uid) -> "Загрузка исследования " <> uid

    // --- Series ---
    En, SeriesPrefix(uid) -> "Series: " <> uid
    Ru, SeriesPrefix(uid) -> "Серия: " <> uid
    En, SeriesBackToStudy -> "Back to Study"
    Ru, SeriesBackToStudy -> "К исследованию"
    En, SeriesInfo -> "Series Information"
    Ru, SeriesInfo -> "Информация о серии"
    En, SeriesLblUid -> "Series UID:"
    Ru, SeriesLblUid -> "UID серии:"
    En, SeriesLblDescription -> "Description:"
    Ru, SeriesLblDescription -> "Описание:"
    En, SeriesLblNumber -> "Number:"
    Ru, SeriesLblNumber -> "Номер:"
    En, SeriesLblAnonUid -> "Anonymous UID:"
    Ru, SeriesLblAnonUid -> "Анонимный UID:"
    En, SeriesLblWorkingFolder -> "Working Folder:"
    Ru, SeriesLblWorkingFolder -> "Рабочая папка:"
    En, SeriesLblStudyUid -> "Study UID:"
    Ru, SeriesLblStudyUid -> "UID исследования:"
    En, SeriesParentStudy -> "Parent Study"
    Ru, SeriesParentStudy -> "Родительское исследование"
    En, SeriesRecords -> "Records"
    Ru, SeriesRecords -> "Записи"
    En, SeriesNoRecords -> "No records found for this series."
    Ru, SeriesNoRecords -> "Записи для этой серии не найдены."
    En, SeriesMsgLoadFailed -> "Failed to load series"
    Ru, SeriesMsgLoadFailed -> "Не удалось загрузить серию"
    En, SeriesLoading(uid) -> "Loading series " <> uid
    Ru, SeriesLoading(uid) -> "Загрузка серии " <> uid

    // --- Record Types ---
    En, RecordTypesNoData -> "No record type data available."
    Ru, RecordTypesNoData -> "Данные о типах записей отсутствуют."
    En, RecordTypesNoFound -> "No record types found."
    Ru, RecordTypesNoFound -> "Типы записей не найдены."
    En, RecordTypePrefix(name) -> "Record Type: " <> name
    Ru, RecordTypePrefix(name) -> "Тип записи: " <> name
    En, RecordTypeBackToList -> "Back to Record Types"
    Ru, RecordTypeBackToList -> "К списку типов"
    En, RecordTypeBtnEdit -> "Edit"
    Ru, RecordTypeBtnEdit -> "Редактировать"
    En, RecordTypeInfo -> "Record Type Information"
    Ru, RecordTypeInfo -> "Информация о типе записи"
    En, RecordTypeLblName -> "Name:"
    Ru, RecordTypeLblName -> "Название:"
    En, RecordTypeLblLabel -> "Label:"
    Ru, RecordTypeLblLabel -> "Метка:"
    En, RecordTypeLblDescription -> "Description:"
    Ru, RecordTypeLblDescription -> "Описание:"
    En, RecordTypeLblLevel -> "Level:"
    Ru, RecordTypeLblLevel -> "Уровень:"
    En, RecordTypeLblRole -> "Role:"
    Ru, RecordTypeLblRole -> "Роль:"
    En, RecordTypeLblMinRecords -> "Min Records:"
    Ru, RecordTypeLblMinRecords -> "Мин. записей:"
    En, RecordTypeLblMaxRecords -> "Max Records:"
    Ru, RecordTypeLblMaxRecords -> "Макс. записей:"
    En, RecordTypeRecords -> "Records"
    Ru, RecordTypeRecords -> "Записи"
    En, RecordTypeNoRecords -> "No records found for this type."
    Ru, RecordTypeNoRecords -> "Записи для этого типа не найдены."
    En, RecordTypeEditPrefix(name) -> "Edit Record Type: " <> name
    Ru, RecordTypeEditPrefix(name) -> "Редактирование типа: " <> name
    En, RecordTypeEditBack -> "Back to Details"
    Ru, RecordTypeEditBack -> "К деталям"
    En, RecordTypeMsgUpdated -> "Record type updated successfully"
    Ru, RecordTypeMsgUpdated -> "Тип записи успешно обновлён"
    En, RecordTypeMsgUpdateFailed -> "Failed to update record type"
    Ru, RecordTypeMsgUpdateFailed -> "Не удалось обновить тип записи"
    En, RecordTypeMsgLoadFailed -> "Failed to load record type"
    Ru, RecordTypeMsgLoadFailed -> "Не удалось загрузить тип записи"
    En, RecordTypeLoading(name) -> "Loading record type " <> name
    Ru, RecordTypeLoading(name) -> "Загрузка типа записи " <> name

    // --- Admin ---
    En, AdminDashboardTitle -> "Admin Dashboard"
    Ru, AdminDashboardTitle -> "Администрирование"
    En, AdminSystemOverview -> "System Overview"
    Ru, AdminSystemOverview -> "Обзор системы"
    En, AdminStatStudies -> "Studies"
    Ru, AdminStatStudies -> "Исследования"
    En, AdminStatRecords -> "Records"
    Ru, AdminStatRecords -> "Записи"
    En, AdminStatUsers -> "Users"
    Ru, AdminStatUsers -> "Пользователи"
    En, AdminStatPatients -> "Patients"
    Ru, AdminStatPatients -> "Пациенты"
    En, AdminRecordsByStatus -> "Records by Status"
    Ru, AdminRecordsByStatus -> "Записи по статусам"
    En, AdminRoleMatrix -> "Role Matrix"
    Ru, AdminRoleMatrix -> "Матрица ролей"
    En, AdminRoleMatrixLoading -> "Loading role matrix..."
    Ru, AdminRoleMatrixLoading -> "Загрузка матрицы ролей..."
    En, AdminNoRoles -> "No roles defined."
    Ru, AdminNoRoles -> "Роли не определены."
    En, AdminBadge -> "admin"
    Ru, AdminBadge -> "админ"
    En, AdminRecords -> "Records"
    Ru, AdminRecords -> "Записи"
    En, AdminNoRecords -> "No records found."
    Ru, AdminNoRecords -> "Записи не найдены."
    En, AdminSelectUser -> "Select user..."
    Ru, AdminSelectUser -> "Выберите пользователя..."
    En, AdminSelectStatus -> "Select status..."
    Ru, AdminSelectStatus -> "Выберите статус..."
    En, AdminMsgUserAssigned -> "User assigned successfully"
    Ru, AdminMsgUserAssigned -> "Пользователь успешно назначен"
    En, AdminMsgUserAssignFailed -> "Failed to assign user to record"
    Ru, AdminMsgUserAssignFailed -> "Не удалось назначить пользователя"
    En, AdminMsgStatusUpdated -> "Status updated successfully"
    Ru, AdminMsgStatusUpdated -> "Статус успешно обновлён"
    En, AdminMsgStatusUpdateFailed -> "Failed to update record status"
    Ru, AdminMsgStatusUpdateFailed -> "Не удалось обновить статус записи"
    En, AdminMsgRoleUpdated -> "Role updated successfully"
    Ru, AdminMsgRoleUpdated -> "Роль успешно обновлена"
    En, AdminMsgRoleUpdateFailed -> "Failed to update role"
    Ru, AdminMsgRoleUpdateFailed -> "Не удалось обновить роль"
    En, AdminMsgLoadFailed -> "Failed to load admin statistics"
    Ru, AdminMsgLoadFailed -> "Не удалось загрузить статистику"
    En, AdminMsgRoleMatrixFailed -> "Failed to load role matrix"
    Ru, AdminMsgRoleMatrixFailed -> "Не удалось загрузить матрицу ролей"

    // --- Execute / Slicer ---
    En, ExecTitle -> "Record Execution"
    Ru, ExecTitle -> "Выполнение записи"
    En, ExecDefaultDesc -> "Complete the record form below"
    Ru, ExecDefaultDesc -> "Заполните форму записи ниже"
    En, ExecBtnOpenSlicer -> "Open in Slicer"
    Ru, ExecBtnOpenSlicer -> "Открыть в Slicer"
    En, ExecBtnComplete -> "Complete Record"
    Ru, ExecBtnComplete -> "Завершить запись"
    En, ExecBtnResubmit -> "Re-submit"
    Ru, ExecBtnResubmit -> "Отправить повторно"
    En, ExecSlicerTitle -> "3D Slicer"
    Ru, ExecSlicerTitle -> "3D Slicer"
    En, ExecSlicerConnected -> "Connected"
    Ru, ExecSlicerConnected -> "Подключён"
    En, ExecSlicerUnreachable -> "Unreachable"
    Ru, ExecSlicerUnreachable -> "Недоступен"
    En, ExecSlicerChecking -> "Checking..."
    Ru, ExecSlicerChecking -> "Проверка..."
    En, ExecNoFormRequired -> "This record does not require form data."
    Ru, ExecNoFormRequired -> "Эта запись не требует заполнения формы."
    En, ExecRecordCompleted -> "Record completed. Re-submit after changes."
    Ru, ExecRecordCompleted -> "Запись завершена. Повторно отправьте после изменений."
    En, ExecNoFormDefined -> "This record does not have a data form defined."
    Ru, ExecNoFormDefined -> "Для этой записи не определена форма данных."
    En, ExecNoDataSubmitted -> "No data submitted."
    Ru, ExecNoDataSubmitted -> "Данные не отправлены."
    En, ExecNoDataYet -> "No data submitted yet"
    Ru, ExecNoDataYet -> "Данные ещё не отправлены"
    En, ExecRecordData -> "Record Data:"
    Ru, ExecRecordData -> "Данные записи:"
    En, ExecLblPatient -> "Patient:"
    Ru, ExecLblPatient -> "Пациент:"
    En, ExecLblStudy -> "Study:"
    Ru, ExecLblStudy -> "Исследование:"
    En, ExecLblSeries -> "Series:"
    Ru, ExecLblSeries -> "Серия:"
    En, ExecLblCreated -> "Created:"
    Ru, ExecLblCreated -> "Создана:"
    En, ExecLblAssigned -> "Assigned to:"
    Ru, ExecLblAssigned -> "Назначена:"
    En, ExecRecordTypeNotFound -> "Record type not found"
    Ru, ExecRecordTypeNotFound -> "Тип записи не найден"
    En, ExecBackToRecords -> "Back to Records"
    Ru, ExecBackToRecords -> "К списку записей"
    En, ExecMsgSlicerOpened -> "Workspace opened in 3D Slicer"
    Ru, ExecMsgSlicerOpened -> "Рабочее пространство открыто в 3D Slicer"
    En, ExecMsgSlicerFailed -> "Failed to open record in Slicer"
    Ru, ExecMsgSlicerFailed -> "Не удалось открыть запись в Slicer"
    En, ExecMsgValidationDone -> "Slicer validation completed"
    Ru, ExecMsgValidationDone -> "Валидация Slicer завершена"
    En, ExecMsgValidationFailed -> "Slicer validation failed"
    Ru, ExecMsgValidationFailed -> "Валидация Slicer не пройдена"
    En, ExecMsgDataSubmitted -> "Record data submitted successfully"
    Ru, ExecMsgDataSubmitted -> "Данные записи успешно отправлены"
    En, ExecMsgCompleted -> "Record completed successfully"
    Ru, ExecMsgCompleted -> "Запись успешно завершена"
    En, ExecMsgResubmitted -> "Record re-submitted successfully"
    Ru, ExecMsgResubmitted -> "Запись успешно повторно отправлена"
    En, ExecMsgDataFailed -> "Failed to submit record data"
    Ru, ExecMsgDataFailed -> "Не удалось отправить данные записи"
    En, ExecMsgCompleteFailed -> "Failed to complete record"
    Ru, ExecMsgCompleteFailed -> "Не удалось завершить запись"
    En, ExecMsgResubmitFailed -> "Failed to re-submit record"
    Ru, ExecMsgResubmitFailed -> "Не удалось повторно отправить запись"
    En, ExecMsgRestartFailed -> "Failed to restart record"
    Ru, ExecMsgRestartFailed -> "Не удалось перезапустить запись"
    En, ExecSlicerNotReachable -> "3D Slicer is not reachable. Is it running?"
    Ru, ExecSlicerNotReachable -> "3D Slicer недоступен. Запущен ли он?"
    En, ExecSlicerError(msg) -> "Slicer error: " <> msg
    Ru, ExecSlicerError(msg) -> "Ошибка Slicer: " <> msg
    En, ExecNetworkError(msg) -> "Network error: " <> msg
    Ru, ExecNetworkError(msg) -> "Ошибка сети: " <> msg
    En, ExecImages(count) -> " (" <> count <> " img)"
    Ru, ExecImages(count) -> " (" <> count <> " изобр.)"

    // --- Modals ---
    En, ModalMarkAsFailed -> "Mark as Failed"
    Ru, ModalMarkAsFailed -> "Отметить как ошибку"
    En, ModalReason -> "Reason:"
    Ru, ModalReason -> "Причина:"
    En, ModalReasonPlaceholder -> "Describe why this record is being failed..."
    Ru, ModalReasonPlaceholder -> "Опишите причину ошибки записи..."
    En, ModalDeletePatientTitle -> "Delete Patient"
    Ru, ModalDeletePatientTitle -> "Удаление пациента"
    En, ModalDeletePatientWarning(id) ->
      "Are you sure you want to delete patient \""
      <> id
      <> "\"? This will permanently delete all associated studies, series, and records. This action cannot be undone."
    Ru, ModalDeletePatientWarning(id) ->
      "Вы уверены, что хотите удалить пациента \""
      <> id
      <> "\"? Все связанные исследования, серии и записи будут безвозвратно удалены."
    En, ModalDeleteStudyTitle -> "Delete Study"
    Ru, ModalDeleteStudyTitle -> "Удаление исследования"
    En, ModalDeleteStudyWarning(uid) ->
      "Are you sure you want to delete study \""
      <> uid
      <> "\"? This will permanently delete all associated series and records. This action cannot be undone."
    Ru, ModalDeleteStudyWarning(uid) ->
      "Вы уверены, что хотите удалить исследование \""
      <> uid
      <> "\"? Все связанные серии и записи будут безвозвратно удалены."
    En, ModalConfirm -> "Confirm"
    Ru, ModalConfirm -> "Подтверждение"
    En, ModalAreYouSure -> "Are you sure?"
    Ru, ModalAreYouSure -> "Вы уверены?"
    En, Page404 -> "404"
    Ru, Page404 -> "404"
    En, PageNotFound -> "Page not found"
    Ru, PageNotFound -> "Страница не найдена"
    En, MsgSessionExpired -> "Session expired. Please log in again."
    Ru, MsgSessionExpired -> "Сессия истекла. Войдите снова."
    En, MsgFailRecordFailed -> "Failed to mark record as failed"
    Ru, MsgFailRecordFailed -> "Не удалось отметить запись как ошибочную"

    // --- Forms ---
    En, FormPatientInfo -> "Patient Information"
    Ru, FormPatientInfo -> "Информация о пациенте"
    En, FormPatientId -> "Patient ID"
    Ru, FormPatientId -> "ID пациента"
    En, FormPatientIdPlaceholder -> "Enter Patient ID"
    Ru, FormPatientIdPlaceholder -> "Введите ID пациента"
    En, FormPatientName -> "Patient Name"
    Ru, FormPatientName -> "Имя пациента"
    En, FormPatientNamePlaceholder -> "Enter Patient Name"
    Ru, FormPatientNamePlaceholder -> "Введите имя пациента"
    En, FormBtnCreatePatient -> "Create Patient"
    Ru, FormBtnCreatePatient -> "Создать пациента"
    En, FormRecordInfo -> "Record Information"
    Ru, FormRecordInfo -> "Информация о записи"
    En, FormRecordType -> "Record Type"
    Ru, FormRecordType -> "Тип записи"
    En, FormSelectRecordType -> "Select record type..."
    Ru, FormSelectRecordType -> "Выберите тип записи..."
    En, FormPatient -> "Patient"
    Ru, FormPatient -> "Пациент"
    En, FormSelectPatient -> "Select patient..."
    Ru, FormSelectPatient -> "Выберите пациента..."
    En, FormStudy -> "Study"
    Ru, FormStudy -> "Исследование"
    En, FormSelectStudy -> "Select study..."
    Ru, FormSelectStudy -> "Выберите исследование..."
    En, FormSeries -> "Series"
    Ru, FormSeries -> "Серия"
    En, FormSelectSeries -> "Select series..."
    Ru, FormSelectSeries -> "Выберите серию..."
    En, FormAssignUser -> "Assign to User"
    Ru, FormAssignUser -> "Назначить пользователю"
    En, FormNoUserUnassigned -> "No user (unassigned)"
    Ru, FormNoUserUnassigned -> "Не назначен"
    En, FormParentRecord -> "Parent Record"
    Ru, FormParentRecord -> "Родительская запись"
    En, FormNoParentRecord -> "No parent record"
    Ru, FormNoParentRecord -> "Нет родительской записи"
    En, FormContextInfo -> "Context Info"
    Ru, FormContextInfo -> "Контекст"
    En, FormContextPlaceholder -> "Optional notes or context"
    Ru, FormContextPlaceholder -> "Необязательные заметки или контекст"
    En, FormBtnCreateRecord -> "Create Record"
    Ru, FormBtnCreateRecord -> "Создать запись"
  }
}

pub fn locale_to_string(locale: Locale) -> String {
  case locale {
    En -> "en"
    Ru -> "ru"
  }
}

pub fn locale_from_string(s: String) -> Locale {
  case s {
    "ru" -> Ru
    _ -> En
  }
}

pub fn locale_label(locale: Locale) -> String {
  case locale {
    En -> "EN"
    Ru -> "RU"
  }
}

pub fn next_locale(locale: Locale) -> Locale {
  case locale {
    En -> Ru
    Ru -> En
  }
}
