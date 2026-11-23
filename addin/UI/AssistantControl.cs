using System;
using System.IO;
using System.Drawing;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using EnvDTE;
using EnvDTE80;
using VS2005QwenRAG.Core;
using VS2005QwenRAG.Network;

namespace VS2005QwenRAG.UI
{
    [ComVisible(true)]
    [Guid("B6252F7A-72B9-4C53-85F7-9E460B08E9D3")]
    [ProgId("VS2005QwenRAG.AssistantControl")]
    [ClassInterface(ClassInterfaceType.AutoDual)]
    public partial class AssistantControl : UserControl
    {
        private DTE2 _dte;
        private DteNavigator _navigator;
        private BackendClient _client;
        private string _currentLang = "ru";
        private bool _mapBuilding = false;
        private System.Windows.Forms.Timer _mapPollTimer = null;

        public DteNavigator Navigator
        {
            get
            {
                if (_navigator == null)
                {
                    DTE2 dte = _dte;
                    if (dte == null) dte = Connect.GlobalDTE;
                    if (dte == null)
                    {
                        string[] progIds = new string[] {
                            "VisualStudio.DTE.12.0",
                            "VisualStudio.DTE.11.0",
                            "VisualStudio.DTE.10.0",
                            "VisualStudio.DTE.9.0",
                            "VisualStudio.DTE.8.0",
                            "VisualStudio.DTE"
                        };
                        foreach (string pid in progIds)
                        {
                            try
                            {
                                dte = (DTE2)Marshal.GetActiveObject(pid);
                                if (dte != null) break;
                            }
                            catch { }
                        }
                    }
                    if (dte != null)
                    {
                        _dte = dte;
                        _navigator = new DteNavigator(_dte, this);
                    }
                }
                return _navigator;
            }
        }

        public AssistantControl()
        {
            InitializeComponent();
            _client = new BackendClient("http://127.0.0.1:18080");
            ApplyLocalization("ru");
            InitWelcomeMessage();
        }

        public void SetDTE(DTE2 dte)
        {
            _dte = dte;
            _navigator = new DteNavigator(_dte, this);
            UpdateSolutionInfo();
            CheckBackendConnection();
        }

        private void InitWelcomeMessage()
        {
            txtChat.Clear();
            AppendChatMessage(
                "🤖 Qwen 7B AI",
                "Добро пожаловать в локальный оффлайн AI-ассистент для Visual Studio 2005!\n" +
                "1. Откройте ваш проект или решение.\n" +
                "2. Нажмите кнопку '🔄 Индексировать', чтобы модель увидела весь код решения.\n" +
                "3. Задавайте вопросы по коду, ищите вызовы функций и зависимости.",
                Color.FromArgb(0, 102, 153)
            );
        }

        public void CheckBackendConnection()
        {
            _client.CheckHealthAsync(delegate(bool isOnline)
            {
                RunOnUIThread(delegate()
                {
                    if (isOnline)
                    {
                        if (_currentLang == "he")
                        {
                            lblStatus.Text = "● שרת: מקוון (Qwen 7B Vulkan)";
                        }
                        else if (_currentLang == "en")
                        {
                            lblStatus.Text = "● Server: Online (Qwen 7B Vulkan)";
                        }
                        else
                        {
                            lblStatus.Text = "● Сервер: В сети (Qwen 7B Vulkan)";
                        }
                        lblStatus.ForeColor = Color.ForestGreen;
                    }
                    else
                    {
                        if (_currentLang == "he")
                        {
                            lblStatus.Text = "● שרת: מנותק";
                        }
                        else if (_currentLang == "en")
                        {
                            lblStatus.Text = "● Server: Offline";
                        }
                        else
                        {
                            lblStatus.Text = "● Сервер: Отключен";
                        }
                        lblStatus.ForeColor = Color.Crimson;
                    }
                });
            });
        }

        public void UpdateSolutionInfo()
        {
            if (_navigator != null)
            {
                string name = _navigator.GetSolutionName();
                if (!string.IsNullOrEmpty(name))
                {
                    lblSolution.Text = (_currentLang == "he" ? "פתרון: " : (_currentLang == "en" ? "Solution: " : "Решение: ")) + name;
                }
                else
                {
                    lblSolution.Text = (_currentLang == "he" ? "פתרון: לא פתוח" : (_currentLang == "en" ? "Solution: none" : "Решение: не открыто"));
                }
            }
        }

        private void comboLang_SelectedIndexChanged(object sender, EventArgs e)
        {
            switch (comboLang.SelectedIndex)
            {
                case 1: _currentLang = "en"; break;
                case 2: _currentLang = "he"; break;
                default: _currentLang = "ru"; break;
            }
            ApplyLocalization(_currentLang);
            UpdateSolutionInfo();
            CheckBackendConnection();
        }

        private void ApplyLocalization(string lang)
        {
            int selectedMode = comboMode.SelectedIndex;
            comboMode.Items.Clear();

            if (lang == "he")
            {
                btnCheckServer.Text = "בדוק";
                btnIndex.Text = "🔄 אנדקס";
                btnInsertSelection.Text = "📋 הדבק קוד";
                btnClearChat.Text = "🧹 נקה צ'אט";
                lblPrompt.Text = "הודעת צ'אט (Enter לשליחה):";
                btnAsk.Text = "שלח";
                lblCitations.Text = " 📍 הפניות לקוד (לחיצה כפולה למעבר לשורה):";

                listCitations.Columns[0].Text = "קובץ";
                listCitations.Columns[1].Text = "שורה";
                listCitations.Columns[2].Text = "סמל / הקשר";

                btnBuildMap.Text = _mapBuilding ? "⏹ עצור" : "🗺️ מפה";
                comboMode.Items.Add("🗺️ Smart Funnel (~1-2 דק')");
                comboMode.Items.Add("🔄 Reducing AI (עד 5 דק')");
                comboMode.Items.Add("🔬 Deep Reducing (עד 10 דק')");
                comboMode.Items.Add("⚡ RAG מהיר (Qwen 7B)");
                comboMode.Items.Add("📍 גרף קריאות (Call Graph)");
                comboMode.Items.Add("📑 מצא הגדרה (Definition)");
                comboMode.Items.Add("🔎 חיפוש מלא בקוד (FTS5)");
            }
            else if (lang == "en")
            {
                btnCheckServer.Text = "Check";
                btnIndex.Text = "🔄 Index Solution";
                btnBuildMap.Text = _mapBuilding ? "⏹ Stop" : "🗺️ Map";
                btnInsertSelection.Text = "📋 Insert";
                btnClearChat.Text = "🧹 Clear";
                lblPrompt.Text = "Chat Message (Enter or Ctrl+Enter to send):";
                btnAsk.Text = "Send";
                lblCitations.Text = " 📍 Code Citations (Double click to navigate):";

                listCitations.Columns[0].Text = "File";
                listCitations.Columns[1].Text = "Line";
                listCitations.Columns[2].Text = "Symbol / Context";

                comboMode.Items.Add("🗺️ Smart Funnel (~1-2m)");
                comboMode.Items.Add("🔄 Reducing AI (up to 5m)");
                comboMode.Items.Add("🔬 Deep Reducing (up to 10m)");
                comboMode.Items.Add("⚡ Fast RAG (Qwen 7B)");
                comboMode.Items.Add("📍 Call Graph Relations");
                comboMode.Items.Add("📑 Find Definition");
                comboMode.Items.Add("🔎 Code Search (FTS5)");
            }
            else
            {
                btnCheckServer.Text = "Проверить";
                btnIndex.Text = "🔄 Индексировать";
                btnBuildMap.Text = _mapBuilding ? "⏹ Стоп" : "🗺️ Карта";
                btnInsertSelection.Text = "📋 Вставить";
                btnClearChat.Text = "🧹 Очистить";
                lblPrompt.Text = "Сообщение чата (Enter или Ctrl+Enter для отправки):";
                btnAsk.Text = "Отправить";
                lblCitations.Text = " 📍 Ссылки на код (Двойной клик — переход к строке):";

                listCitations.Columns[0].Text = "Файл";
                listCitations.Columns[1].Text = "Строка";
                listCitations.Columns[2].Text = "Символ / Сигнатура / Контекст";

                comboMode.Items.Add("🗺️ Smart Funnel (~1-2 мин)");
                comboMode.Items.Add("🔄 Reducing AI (до 5 мин)");
                comboMode.Items.Add("🔬 Deep Reducing (до 10 мин)");
                comboMode.Items.Add("⚡ Быстрый RAG (Qwen 7B)");
                comboMode.Items.Add("📍 Поиск связей (Call Graph)");
                comboMode.Items.Add("📑 Найти определение");
                comboMode.Items.Add("🔎 Поиск по коду (FTS5)");
            }

            comboMode.SelectedIndex = (selectedMode >= 0 && selectedMode < comboMode.Items.Count) ? selectedMode : 0;
        }

        private void AppendChatMessage(string senderName, string message, Color headerColor)
        {
            txtChat.SelectionStart = txtChat.TextLength;
            txtChat.SelectionLength = 0;

            txtChat.SelectionColor = headerColor;
            txtChat.SelectionFont = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            txtChat.AppendText(string.Format("{0} [{1:HH:mm:ss}]:\n", senderName, DateTime.Now));

            txtChat.SelectionColor = Color.FromArgb(30, 30, 30);
            txtChat.SelectionFont = new Font("Consolas", 9.5f, FontStyle.Regular);
            txtChat.AppendText(message.Trim() + "\n\n");

            txtChat.SelectionColor = Color.FromArgb(215, 215, 220);
            txtChat.AppendText("──────────────────────────────────────────────────\n\n");

            txtChat.SelectionStart = txtChat.TextLength;
            txtChat.ScrollToCaret();
        }

        private void btnClearChat_Click(object sender, EventArgs e)
        {
            txtChat.Clear();
            listCitations.Items.Clear();
            InitWelcomeMessage();
        }

        private void btnCheckServer_Click(object sender, EventArgs e)
        {
            lblStatus.Text = "● Сервер: Проверка...";
            lblStatus.ForeColor = Color.DarkOrange;
            CheckBackendConnection();
        }

        private void btnBuildMap_Click(object sender, EventArgs e)
        {
            if (_mapBuilding)
            {
                _client.CancelMapBuildAsync(delegate(bool ok, string st, int mapped, int total, double pct, string msg)
                {
                    RunOnUIThread(delegate()
                    {
                        AppendChatMessage("🗺️ Карта проекта", ok ? msg : ("Ошибка остановки: " + msg), Color.DarkSlateBlue);
                    });
                });
                return;
            }

            _mapBuilding = true;
            btnBuildMap.Text = (_currentLang == "en") ? "⏹ Stop" : "⏹ Стоп";
            progressBar.Visible = true;
            AppendChatMessage(
                "🗺️ Карта проекта",
                "Запущена инкрементальная сборка семантической карты проекта (кэш по mtime, можно остановить в любой момент)...",
                Color.DarkSlateBlue
            );

            _client.StartMapBuildAsync(delegate(bool ok, string st, int mapped, int total, double pct, string msg)
            {
                RunOnUIThread(delegate()
                {
                    if (!ok)
                    {
                        _mapBuilding = false;
                        progressBar.Visible = false;
                        btnBuildMap.Text = (_currentLang == "en") ? "🗺️ Map" : "🗺️ Карта";
                        AppendChatMessage("❌ Ошибка карты", msg, Color.Crimson);
                        return;
                    }
                    StartMapPolling();
                });
            });
        }

        private void StartMapPolling()
        {
            if (_mapPollTimer == null)
            {
                _mapPollTimer = new System.Windows.Forms.Timer();
                _mapPollTimer.Interval = 2500;
                _mapPollTimer.Tick += new EventHandler(MapPollTimer_Tick);
            }
            _mapPollTimer.Start();
        }

        private void MapPollTimer_Tick(object sender, EventArgs e)
        {
            _client.GetMapStatusAsync(delegate(bool ok, string st, int mapped, int total, double pct, string msg)
            {
                RunOnUIThread(delegate()
                {
                    if (!ok)
                    {
                        if (_mapPollTimer != null) _mapPollTimer.Stop();
                        _mapBuilding = false;
                        progressBar.Visible = false;
                        btnBuildMap.Text = (_currentLang == "en") ? "🗺️ Map" : "🗺️ Карта";
                        return;
                    }

                    if (st == "running")
                    {
                        lblSolution.Text = string.Format("🗺️ Карта: {0}/{1} ({2:F0}%) {3}", mapped, total, pct, msg);
                    }
                    else
                    {
                        if (_mapPollTimer != null) _mapPollTimer.Stop();
                        _mapBuilding = false;
                        progressBar.Visible = false;
                        btnBuildMap.Text = (_currentLang == "en") ? "🗺️ Map" : "🗺️ Карта";
                        UpdateSolutionInfo();
                        AppendChatMessage(
                            st == "completed" ? "✅ Карта проекта готова" : "🗺️ Статус карты",
                            string.Format("Статус: {0} | Покрытие: {1}/{2} файлов ({3:F1}%)", st, mapped, total, pct),
                            st == "completed" ? Color.ForestGreen : Color.DarkSlateBlue
                        );
                    }
                });
            });
        }

        private void btnSelectModel_Click(object sender, EventArgs e)
        {
            using (OpenFileDialog ofd = new OpenFileDialog())
            {
                ofd.Title = "Select Local .gguf Model";
                ofd.Filter = "GGUF Models (*.gguf)|*.gguf|All Files (*.*)|*.*";
                if (ofd.ShowDialog() == DialogResult.OK)
                {
                    string selectedPath = ofd.FileName;
                    btnSelectModel.Enabled = false;
                    AppendChatMessage("⚙️ Система", "Переключение модели: " + selectedPath, Color.DarkSlateBlue);
                    _client.SelectModelAsync(selectedPath, delegate(bool ok, string msg)
                    {
                        RunOnUIThread(delegate()
                        {
                            btnSelectModel.Enabled = true;
                            AppendChatMessage(ok ? "✅ Модель активна" : "❌ Ошибка модели", msg, ok ? Color.ForestGreen : Color.Crimson);
                            CheckBackendConnection();
                        });
                    });
                }
            }
        }

        private void btnInsertSelection_Click(object sender, EventArgs e)
        {
            if (_navigator == null) return;

            string file;
            int line;
            string text;
            _navigator.GetActiveDocumentContext(out file, out line, out text);

            if (!string.IsNullOrEmpty(text))
            {
                txtQuestion.Text = text.Trim();
                txtQuestion.Focus();
                txtQuestion.SelectAll();
            }
            else
            {
                MessageBox.Show("В активном окне кода нет выделенного фрагмента.", "Qwen Assistant", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }

        private void btnIndex_Click(object sender, EventArgs e)
        {
            string folder = string.Empty;
            if (_navigator != null)
            {
                folder = _navigator.GetSolutionDirectory();
            }

            if (string.IsNullOrEmpty(folder) || !Directory.Exists(folder))
            {
                using (FolderBrowserDialog fbd = new FolderBrowserDialog())
                {
                    fbd.Description = (_currentLang == "he") ? "בחר תיקיית פרויקט לאינדוקס:" :
                        ((_currentLang == "en") ? "Select project folder to index:" : "Выберите папку вашего проекта для индексации:");
                    if (fbd.ShowDialog() == DialogResult.OK)
                    {
                        folder = fbd.SelectedPath;
                    }
                    else return;
                }
            }

            progressBar.Visible = true;
            btnIndex.Enabled = false;

            AppendChatMessage(
                "⚙️ Система",
                string.Format("Начата индексация проекта: {0}\nФильтрация бинарников, построение AST и графа вызовов...", folder),
                Color.FromArgb(120, 120, 130)
            );

            _client.IndexDirectoryAsync(folder, delegate(IndexResult res)
            {
                RunOnUIThread(delegate()
                {
                    progressBar.Visible = false;
                    btnIndex.Enabled = true;

                    if (res.Success)
                    {
                        AppendChatMessage(
                            "✅ Индексация завершена",
                            string.Format(
                                "Файлов исходного кода: {0}\n" +
                                "Символов (функций, классов, макросов): {1}\n" +
                                "Связей в графе вызовов: {2}\n" +
                                "Время: {3:F2} сек.\n\n" +
                                "Теперь модель видит архитектуру и все функции вашего проекта!",
                                res.FilesCount, res.SymbolsCount, res.CallsCount, res.DurationSeconds),
                            Color.ForestGreen
                        );
                    }
                    else
                    {
                        AppendChatMessage("❌ Ошибка индексации", res.Message, Color.Crimson);
                    }
                });
            });
        }

        private void txtQuestion_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Enter && !e.Shift)
            {
                e.SuppressKeyPress = true;
                btnAsk_Click(this, EventArgs.Empty);
            }
        }

        private void btnAsk_Click(object sender, EventArgs e)
        {
            string question = txtQuestion.Text.Trim();
            if (string.IsNullOrEmpty(question)) return;

            txtQuestion.Clear();

            string userLabel = (_currentLang == "he") ? "👤 אתה" : ((_currentLang == "en") ? "👤 You" : "👤 Вы");
            AppendChatMessage(userLabel, question, Color.FromArgb(0, 102, 204));

            string mode = "funnel";
            switch (comboMode.SelectedIndex)
            {
                case 0: mode = "funnel"; break;
                case 1: mode = "reduce"; break;
                case 2: mode = "reduce_deep"; break;
                case 3: mode = "fast"; break;
                case 4: mode = "callers"; break;
                case 5: mode = "definition"; break;
                case 6: mode = "grep"; break;
                default: mode = "funnel"; break;
            }

            string currentFile = string.Empty;
            int currentLine = 1;
            string selectedCode = string.Empty;

            if (_navigator != null)
            {
                _navigator.GetActiveDocumentContext(out currentFile, out currentLine, out selectedCode);
            }

            progressBar.Visible = true;
            btnAsk.Enabled = false;
            listCitations.Items.Clear();

            _client.AskAsync(question, mode, currentFile, currentLine, selectedCode, _currentLang, delegate(QueryResult res)
            {
                RunOnUIThread(delegate()
                {
                    progressBar.Visible = false;
                    btnAsk.Enabled = true;

                    if (res.Success)
                    {
                        AppendChatMessage("🤖 Qwen 7B", res.Answer, Color.FromArgb(0, 130, 80));

                        if (res.Citations.Count > 0)
                        {
                            foreach (CodeCitation cit in res.Citations)
                            {
                                ListViewItem item = new ListViewItem(cit.FileName);
                                item.SubItems.Add(cit.Line.ToString());
                                item.SubItems.Add(!string.IsNullOrEmpty(cit.Symbol) ? cit.Symbol : cit.Snippet);
                                item.Tag = cit;
                                listCitations.Items.Add(item);
                            }
                        }
                    }
                    else
                    {
                        AppendChatMessage("❌ Ошибка", (res.Error ?? res.Answer), Color.Crimson);
                    }
                });
            });
        }

        public void listCitations_ItemActivate(object sender, EventArgs e)
        {
            OpenSelectedCitation();
        }

        private void listCitations_DoubleClick(object sender, EventArgs e)
        {
            OpenSelectedCitation();
        }

        private void OpenSelectedCitation()
        {
            if (listCitations.SelectedItems.Count > 0)
            {
                CodeCitation cit = listCitations.SelectedItems[0].Tag as CodeCitation;
                if (cit != null && !string.IsNullOrEmpty(cit.FilePath))
                {
                    DteNavigator nav = this.Navigator;
                    if (nav != null)
                    {
                        nav.NavigateToFileAndLine(cit.FilePath, cit.Line);
                    }
                }
            }
        }

        public void txtChat_MouseClick(object sender, MouseEventArgs e)
        {
            try
            {
                int charIndex = txtChat.GetCharIndexFromPosition(e.Location);
                if (charIndex >= 0 && charIndex < txtChat.TextLength)
                {
                    int lineIndex = txtChat.GetLineFromCharIndex(charIndex);
                    int lineStart = txtChat.GetFirstCharIndexFromLine(lineIndex);
                    string lineText = txtChat.Lines[lineIndex];
                    int offset = charIndex - lineStart;

                    System.Text.RegularExpressions.MatchCollection matches = 
                        System.Text.RegularExpressions.Regex.Matches(lineText, @"\[([A-Za-z0-9_.\-\\]+\.(?:cpp|c|h|hpp|inl)):(\d+)\]");
                    foreach (System.Text.RegularExpressions.Match m in matches)
                    {
                        if (offset >= m.Index && offset <= m.Index + m.Length)
                        {
                            string fname = m.Groups[1].Value;
                            int lineNum = int.Parse(m.Groups[2].Value);
                            DteNavigator nav = this.Navigator;
                            if (nav != null)
                            {
                                nav.NavigateToFileAndLine(fname, lineNum);
                            }
                            break;
                        }
                    }
                }
            }
            catch { }
        }

        private void AssistantControl_Resize(object sender, EventArgs e)
        {
            int w = this.ClientSize.Width;
            if (w > 120)
            {
                txtQuestion.Width = w - 95;
                btnAsk.Left = w - 85;
                lblSolution.Width = w - 20;
            }
        }

        private void RunOnUIThread(MethodInvoker action)
        {
            if (this.InvokeRequired)
            {
                this.BeginInvoke(action);
            }
            else
            {
                action();
            }
        }
    }
}
