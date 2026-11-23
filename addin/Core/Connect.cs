using System;
using System.IO;
using System.Drawing;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using Extensibility;
using EnvDTE;
using EnvDTE80;
using Microsoft.VisualStudio.CommandBars;
using VS2005QwenRAG.UI;

namespace VS2005QwenRAG.Core
{
    [GuidAttribute("B6252F7A-72B9-4C53-85F7-9E460B08E9D1"), ProgId("VS2005QwenRAG.Connect")]
    public class Connect : IDTExtensibility2, IDTCommandTarget
    {
        private static DTE2 _globalDte;
        public static DTE2 GlobalDTE
        {
            get { return _globalDte; }
            set { _globalDte = value; }
        }
        private DTE2 _applicationObject;
        private AddIn _addInInstance;
        private Window _toolWindow;
        private AssistantControl _assistantControl;
        private Form _standaloneForm;

        private const string CommandName = "VS2005QwenRAG.Connect.OpenAssistant";

        public void OnConnection(object application, ext_ConnectMode connectMode, object addInInst, ref Array custom)
        {
            _applicationObject = (DTE2)application;
            GlobalDTE = _applicationObject;
            _addInInstance = (AddIn)addInInst;

            if (connectMode == ext_ConnectMode.ext_cm_UISetup || connectMode == ext_ConnectMode.ext_cm_Startup)
            {
                SetupMenuCommand();
            }
        }

        private void SetupMenuCommand()
        {
            try
            {
                Commands2 commands = (Commands2)_applicationObject.Commands;
                CommandBar menuBar = ((CommandBars)_applicationObject.CommandBars)["MenuBar"];
                CommandBarControl toolsMenu = menuBar.Controls["Tools"];
                CommandBarPopup toolsPopup = (CommandBarPopup)toolsMenu;

                try
                {
                    Command cmd = commands.Item(CommandName, 0);
                    if (cmd != null)
                    {
                        cmd.Delete();
                    }
                }
                catch { }

                object[] contextGuids = new object[] { };
                Command createdCmd = commands.AddNamedCommand2(
                    _addInInstance,
                    "OpenAssistant",
                    "Offline C++ Coding Agent",
                    "Open local Map-Reduce & Call-Graph AI coding agent",
                    true,
                    59,
                    ref contextGuids,
                    (int)vsCommandStatus.vsCommandStatusSupported | (int)vsCommandStatus.vsCommandStatusEnabled,
                    (int)vsCommandStyle.vsCommandStylePictAndText,
                    vsCommandControlType.vsCommandControlTypeButton);

                if (createdCmd != null && toolsPopup != null)
                {
                    createdCmd.AddControl(toolsPopup.CommandBar, 1);
                }
            }
            catch { }
        }

        public void ShowAssistant()
        {
            try
            {
                if (_toolWindow == null)
                {
                    object controlObj = null;
                    string assemblyPath = System.Reflection.Assembly.GetExecutingAssembly().Location;
                    Windows2 windows2 = (Windows2)_applicationObject.Windows;

                    _toolWindow = windows2.CreateToolWindow2(
                        _addInInstance,
                        assemblyPath,
                        "VS2005QwenRAG.UI.AssistantControl",
                        "Offline C++ Coding Agent",
                        "{B6252F7A-72B9-4C53-85F7-9E460B08E9D5}",
                        ref controlObj);

                    _assistantControl = controlObj as AssistantControl;
                    if (_assistantControl != null)
                    {
                        _assistantControl.SetDTE(_applicationObject);
                    }
                }

                if (_toolWindow != null)
                {
                    _toolWindow.Visible = true;
                    return;
                }
            }
            catch
            {
                ShowFloatingWindow();
            }
        }

        private void ShowFloatingWindow()
        {
            if (_standaloneForm == null || _standaloneForm.IsDisposed)
            {
                _standaloneForm = new Form();
                _standaloneForm.Text = "Offline C++ Coding Agent";
                _standaloneForm.Size = new Size(500, 620);
                _standaloneForm.StartPosition = FormStartPosition.CenterScreen;

                AssistantControl ctrl = new AssistantControl();
                ctrl.Dock = DockStyle.Fill;
                ctrl.SetDTE(_applicationObject);
                _standaloneForm.Controls.Add(ctrl);
            }
            _standaloneForm.Show();
            _standaloneForm.BringToFront();
        }

        public void QueryStatus(string commandName, vsCommandStatusTextWanted neededText, ref vsCommandStatus status, ref object commandText)
        {
            if (neededText == vsCommandStatusTextWanted.vsCommandStatusTextWantedNone)
            {
                if (commandName == CommandName || commandName.EndsWith(".OpenAssistant"))
                {
                    status = vsCommandStatus.vsCommandStatusSupported | vsCommandStatus.vsCommandStatusEnabled;
                    return;
                }
            }
        }

        public void Exec(string commandName, vsCommandExecOption executeOption, ref object varIn, ref object varOut, ref bool handled)
        {
            handled = false;
            if (executeOption == vsCommandExecOption.vsCommandExecOptionDoDefault)
            {
                if (commandName == CommandName || commandName.EndsWith(".OpenAssistant"))
                {
                    ShowAssistant();
                    handled = true;
                }
            }
        }

        public void OnDisconnection(ext_DisconnectMode disconnectMode, ref Array custom)
        {
            if (_standaloneForm != null && !_standaloneForm.IsDisposed)
            {
                _standaloneForm.Close();
                _standaloneForm = null;
            }
        }

        public void OnAddInsUpdate(ref Array custom) { }

        public void OnStartupComplete(ref Array custom)
        {
            try
            {
                ShowAssistant();
            }
            catch { }
        }

        public void OnBeginShutdown(ref Array custom) { }
    }
}
