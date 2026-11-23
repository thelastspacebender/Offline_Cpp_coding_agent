using System.Drawing;
using System.Windows.Forms;

namespace VS2005QwenRAG.UI
{
    public partial class AssistantControl
    {
        private System.ComponentModel.IContainer components = null;

        protected override void Dispose(bool disposing)
        {
            if (disposing && (components != null))
            {
                components.Dispose();
            }
            base.Dispose(disposing);
        }

        private void InitializeComponent()
        {
            this.panelTop = new Panel();
            this.lblStatus = new Label();
            this.btnCheckServer = new Button();
            this.comboLang = new ComboBox();
            this.btnIndex = new Button();
            this.lblSolution = new Label();
            this.comboMode = new ComboBox();
            this.btnSelectModel = new Button();
            this.btnInsertSelection = new Button();
            this.btnClearChat = new Button();

            this.panelBottom = new Panel();
            this.lblPrompt = new Label();
            this.progressBar = new ProgressBar();
            this.txtQuestion = new TextBox();
            this.btnAsk = new Button();

            this.panelCenter = new Panel();
            this.splitContainer = new SplitContainer();
            this.txtChat = new RichTextBox();
            this.lblCitations = new Label();
            this.listCitations = new ListView();

            this.SuspendLayout();

            // panelTop
            this.panelTop.Dock = DockStyle.Top;
            this.panelTop.Height = 92;
            this.panelTop.BackColor = Color.FromArgb(243, 244, 246);
            this.panelTop.Padding = new Padding(8);

            this.lblStatus.Location = new Point(8, 8);
            this.lblStatus.Size = new Size(160, 22);
            this.lblStatus.Text = "● Сервер: Проверка...";
            this.lblStatus.ForeColor = Color.DarkOrange;
            this.lblStatus.Font = new Font("Segoe UI", 8.5f, FontStyle.Bold);

            this.btnCheckServer.Location = new Point(172, 6);
            this.btnCheckServer.Size = new Size(68, 24);
            this.btnCheckServer.Text = "Проверить";
            this.btnCheckServer.Font = new Font("Segoe UI", 8f);
            this.btnCheckServer.Click += new System.EventHandler(this.btnCheckServer_Click);

            this.comboLang.Location = new Point(244, 6);
            this.comboLang.Size = new Size(88, 24);
            this.comboLang.DropDownStyle = ComboBoxStyle.DropDownList;
            this.comboLang.Font = new Font("Segoe UI", 8.5f);
            this.comboLang.Items.Add("🇷🇺 RU");
            this.comboLang.Items.Add("🇬🇧 EN");
            this.comboLang.Items.Add("🇮🇱 HE");
            this.comboLang.SelectedIndex = 0;
            this.comboLang.SelectedIndexChanged += new System.EventHandler(this.comboLang_SelectedIndexChanged);

            this.btnIndex.Location = new Point(336, 6);
            this.btnIndex.Size = new Size(130, 24);
            this.btnIndex.Text = "🔄 Индексировать";
            this.btnIndex.BackColor = Color.FromArgb(0, 122, 204);
            this.btnIndex.ForeColor = Color.White;
            this.btnIndex.FlatStyle = FlatStyle.Flat;
            this.btnIndex.Font = new Font("Segoe UI", 8f, FontStyle.Bold);
            this.btnIndex.Click += new System.EventHandler(this.btnIndex_Click);

            this.lblSolution.Location = new Point(8, 34);
            this.lblSolution.Size = new Size(458, 20);
            this.lblSolution.Text = "Решение: не открыто";
            this.lblSolution.AutoEllipsis = true;
            this.lblSolution.Font = new Font("Segoe UI", 8.5f);

            this.comboMode.Location = new Point(8, 58);
            this.comboMode.Size = new Size(192, 24);
            this.comboMode.DropDownStyle = ComboBoxStyle.DropDownList;
            this.comboMode.Font = new Font("Segoe UI", 8.5f);
            this.comboMode.Items.Add("🔄 Reducing AI (до 5 мин)");
            this.comboMode.Items.Add("🔬 Deep Reducing (до 10 мин)");
            this.comboMode.Items.Add("⚡ Быстрый RAG (LLM)");
            this.comboMode.Items.Add("📍 Поиск связей (Call Graph)");
            this.comboMode.Items.Add("📑 Найти определение");
            this.comboMode.Items.Add("🔎 Поиск по коду (FTS5)");
            this.comboMode.SelectedIndex = 0;

            this.btnSelectModel.Location = new Point(204, 57);
            this.btnSelectModel.Size = new Size(58, 25);
            this.btnSelectModel.Text = "📂 .gguf";
            this.btnSelectModel.Font = new Font("Segoe UI", 8f);
            this.btnSelectModel.Click += new System.EventHandler(this.btnSelectModel_Click);

            this.btnInsertSelection.Location = new Point(266, 57);
            this.btnInsertSelection.Size = new Size(110, 25);
            this.btnInsertSelection.Text = "📋 Вставить код";
            this.btnInsertSelection.Font = new Font("Segoe UI", 8f);
            this.btnInsertSelection.Click += new System.EventHandler(this.btnInsertSelection_Click);

            this.btnClearChat.Location = new Point(380, 57);
            this.btnClearChat.Size = new Size(86, 25);
            this.btnClearChat.Text = "🧹 Очистить";
            this.btnClearChat.Font = new Font("Segoe UI", 8f);
            this.btnClearChat.Click += new System.EventHandler(this.btnClearChat_Click);

            this.panelTop.Controls.Add(this.lblStatus);
            this.panelTop.Controls.Add(this.btnCheckServer);
            this.panelTop.Controls.Add(this.comboLang);
            this.panelTop.Controls.Add(this.btnIndex);
            this.panelTop.Controls.Add(this.lblSolution);
            this.panelTop.Controls.Add(this.comboMode);
            this.panelTop.Controls.Add(this.btnSelectModel);
            this.panelTop.Controls.Add(this.btnInsertSelection);
            this.panelTop.Controls.Add(this.btnClearChat);

            // panelBottom
            this.panelBottom.Dock = DockStyle.Bottom;
            this.panelBottom.Height = 105;
            this.panelBottom.BackColor = Color.FromArgb(248, 249, 250);
            this.panelBottom.Padding = new Padding(8);

            this.lblPrompt.Location = new Point(8, 4);
            this.lblPrompt.Size = new Size(330, 18);
            this.lblPrompt.Text = "Сообщение чата (Enter или Ctrl+Enter для отправки):";
            this.lblPrompt.Font = new Font("Segoe UI", 8.5f, FontStyle.Bold);

            this.progressBar.Location = new Point(342, 4);
            this.progressBar.Size = new Size(124, 16);
            this.progressBar.Style = ProgressBarStyle.Marquee;
            this.progressBar.Visible = false;

            this.txtQuestion.Location = new Point(8, 24);
            this.txtQuestion.Size = new Size(376, 74);
            this.txtQuestion.Multiline = true;
            this.txtQuestion.ScrollBars = ScrollBars.Vertical;
            this.txtQuestion.Font = new Font("Segoe UI", 9.5f);
            this.txtQuestion.KeyDown += new KeyEventHandler(this.txtQuestion_KeyDown);

            this.btnAsk.Location = new Point(388, 24);
            this.btnAsk.Size = new Size(78, 74);
            this.btnAsk.Text = "Отправить";
            this.btnAsk.BackColor = Color.FromArgb(0, 122, 204);
            this.btnAsk.ForeColor = Color.White;
            this.btnAsk.FlatStyle = FlatStyle.Flat;
            this.btnAsk.Font = new Font("Segoe UI", 9, FontStyle.Bold);
            this.btnAsk.Click += new System.EventHandler(this.btnAsk_Click);

            this.panelBottom.Controls.Add(this.lblPrompt);
            this.panelBottom.Controls.Add(this.progressBar);
            this.panelBottom.Controls.Add(this.txtQuestion);
            this.panelBottom.Controls.Add(this.btnAsk);

            // panelCenter
            this.panelCenter.Dock = DockStyle.Fill;

            this.splitContainer.Dock = DockStyle.Fill;
            this.splitContainer.Orientation = Orientation.Horizontal;
            this.splitContainer.SplitterDistance = 270;

            // txtChat (Chat conversation history)
            this.txtChat.Dock = DockStyle.Fill;
            this.txtChat.ReadOnly = true;
            this.txtChat.BackColor = Color.FromArgb(253, 254, 255);
            this.txtChat.Font = new Font("Consolas", 9.5f);
            this.txtChat.ScrollBars = RichTextBoxScrollBars.Vertical;
            this.txtChat.MouseClick += new System.Windows.Forms.MouseEventHandler(this.txtChat_MouseClick);

            Panel citPanel = new Panel();
            citPanel.Dock = DockStyle.Fill;

            this.lblCitations.Dock = DockStyle.Top;
            this.lblCitations.Height = 22;
            this.lblCitations.Text = " 📍 Ссылки на код (Двойной клик — переход к строке в VS 2005):";
            this.lblCitations.Font = new Font("Segoe UI", 8.5f, FontStyle.Bold);
            this.lblCitations.BackColor = Color.FromArgb(235, 238, 242);

            this.listCitations.Dock = DockStyle.Fill;
            this.listCitations.View = View.Details;
            this.listCitations.FullRowSelect = true;
            this.listCitations.GridLines = true;
            this.listCitations.Font = new Font("Segoe UI", 8.5f);
            this.listCitations.Columns.Add("Файл", 150);
            this.listCitations.Columns.Add("Строка", 55);
            this.listCitations.Columns.Add("Символ / Сигнатура / Контекст", 260);
            this.listCitations.DoubleClick += new System.EventHandler(this.listCitations_DoubleClick);
            this.listCitations.ItemActivate += new System.EventHandler(this.listCitations_ItemActivate);

            citPanel.Controls.Add(this.listCitations);
            citPanel.Controls.Add(this.lblCitations);

            this.splitContainer.Panel1.Controls.Add(this.txtChat);
            this.splitContainer.Panel2.Controls.Add(citPanel);
            this.panelCenter.Controls.Add(this.splitContainer);

            this.Controls.Add(this.panelCenter);
            this.Controls.Add(this.panelBottom);
            this.Controls.Add(this.panelTop);

            this.Size = new Size(475, 540);
            this.Resize += new System.EventHandler(this.AssistantControl_Resize);
            this.ResumeLayout(false);
        }

        private Panel panelTop;
        private Panel panelBottom;
        private Panel panelCenter;
        private Label lblStatus;
        private Label lblSolution;
        private Button btnIndex;
        private Button btnCheckServer;
        private ComboBox comboLang;
        private ComboBox comboMode;
        private Button btnSelectModel;
        private Button btnInsertSelection;
        private Button btnClearChat;
        private TextBox txtQuestion;
        private Button btnAsk;
        private ProgressBar progressBar;
        private SplitContainer splitContainer;
        private RichTextBox txtChat;
        private ListView listCitations;
        private Label lblCitations;
        private Label lblPrompt;
    }
}
