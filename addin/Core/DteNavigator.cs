using System;
using System.IO;
using System.Windows.Forms;
using EnvDTE;
using EnvDTE80;

namespace VS2005QwenRAG.Core
{
    /// <summary>
    /// Thread-safe wrapper for manipulating Visual Studio 2005 DTE.
    /// Ensures all calls occur on the IDE's main STA thread.
    /// </summary>
    public class DteNavigator
    {
        private readonly DTE2 _dte;
        private readonly Control _syncControl;

        public DteNavigator(DTE2 dte, Control syncControl)
        {
            _dte = dte;
            _syncControl = syncControl;
        }

        public string GetSolutionDirectory()
        {
            string path = string.Empty;
            ExecuteOnUI(delegate()
            {
                try
                {
                    if (_dte != null && _dte.Solution != null)
                    {
                        if (!string.IsNullOrEmpty(_dte.Solution.FullName))
                        {
                            path = Path.GetDirectoryName(_dte.Solution.FullName);
                        }
                        else if (_dte.Solution.Projects != null && _dte.Solution.Projects.Count > 0)
                        {
                            Project prj = _dte.Solution.Projects.Item(1);
                            if (prj != null && !string.IsNullOrEmpty(prj.FullName))
                            {
                                path = Path.GetDirectoryName(prj.FullName);
                            }
                        }
                    }

                    if (string.IsNullOrEmpty(path) && _dte != null && _dte.ActiveDocument != null)
                    {
                        path = Path.GetDirectoryName(_dte.ActiveDocument.FullName);
                    }
                }
                catch { }
            });
            return path;
        }

        public string GetSolutionName()
        {
            string name = string.Empty;
            ExecuteOnUI(delegate()
            {
                try
                {
                    if (_dte != null && _dte.Solution != null && !string.IsNullOrEmpty(_dte.Solution.FullName))
                    {
                        name = Path.GetFileName(_dte.Solution.FullName);
                    }
                    else if (_dte != null && _dte.Solution != null && _dte.Solution.Projects != null && _dte.Solution.Projects.Count > 0)
                    {
                        Project prj = _dte.Solution.Projects.Item(1);
                        if (prj != null && !string.IsNullOrEmpty(prj.Name))
                        {
                            name = prj.Name + " (Project)";
                        }
                    }
                    else if (_dte != null && _dte.ActiveDocument != null)
                    {
                        name = Path.GetFileName(_dte.ActiveDocument.FullName);
                    }
                }
                catch { }
            });
            return name;
        }

        public string GetActiveDocumentShortInfo()
        {
            string info = string.Empty;
            ExecuteOnUI(delegate()
            {
                try
                {
                    if (_dte != null && _dte.ActiveDocument != null)
                    {
                        string fname = Path.GetFileName(_dte.ActiveDocument.FullName);
                        int line = 1;
                        TextSelection sel = _dte.ActiveDocument.Selection as TextSelection;
                        if (sel != null)
                        {
                            line = sel.CurrentLine;
                        }
                        info = string.Format("{0}:{1}", fname, line);
                    }
                }
                catch { }
            });
            return info;
        }

        public void GetActiveDocumentContext(out string currentFile, out int currentLine, out string selectedText)
        {
            string file = string.Empty;
            int line = 1;
            string text = string.Empty;

            ExecuteOnUI(delegate()
            {
                try
                {
                    if (_dte != null && _dte.ActiveDocument != null)
                    {
                        file = _dte.ActiveDocument.FullName;
                        TextSelection sel = _dte.ActiveDocument.Selection as TextSelection;
                        if (sel != null)
                        {
                            line = sel.CurrentLine;
                            text = sel.Text;

                            // If user hasn't selected text, automatically grab lines surrounding cursor!
                            if (string.IsNullOrEmpty(text))
                            {
                                try
                                {
                                    TextDocument textDoc = _dte.ActiveDocument.Object("TextDocument") as TextDocument;
                                    if (textDoc != null)
                                    {
                                        EditPoint epStart = textDoc.CreateEditPoint(null);
                                        int startLine = Math.Max(1, line - 25);
                                        int endLine = line + 25;
                                        epStart.MoveToLineAndOffset(startLine, 1);

                                        EditPoint epEnd = textDoc.CreateEditPoint(null);
                                        epEnd.MoveToLineAndOffset(endLine, 1);
                                        text = epStart.GetText(epEnd);
                                    }
                                }
                                catch { }
                            }
                        }
                    }
                }
                catch { }
            });

            currentFile = file;
            currentLine = line;
            selectedText = text;
        }

        public bool NavigateToFileAndLine(string filePath, int line)
        {
            bool success = false;
            ExecuteOnUI(delegate()
            {
                try
                {
                    if (_dte != null && !string.IsNullOrEmpty(filePath))
                    {
                        string normalized = filePath.Replace('/', '\\');

                        // Fallback 1: Resolve against solution directory if relative or not found
                        if (!File.Exists(normalized))
                        {
                            string solDir = GetSolutionDirectory();
                            if (!string.IsNullOrEmpty(solDir))
                            {
                                string candidate = Path.Combine(solDir, normalized);
                                if (File.Exists(candidate))
                                {
                                    normalized = candidate;
                                }
                                else
                                {
                                    // Search by filename within the solution tree
                                    string fname = Path.GetFileName(normalized);
                                    try
                                    {
                                        string[] found = Directory.GetFiles(solDir, fname, SearchOption.AllDirectories);
                                        if (found != null && found.Length > 0)
                                        {
                                            normalized = found[0];
                                        }
                                    }
                                    catch { }
                                }
                            }
                        }

                        if (File.Exists(normalized))
                        {
                            Window w = null;
                            try
                            {
                                w = _dte.ItemOperations.OpenFile(normalized, Constants.vsViewKindCode);
                            }
                            catch
                            {
                                try
                                {
                                    Document doc = _dte.Documents.Open(normalized, "Text", false);
                                    if (doc != null) w = doc.ActiveWindow;
                                }
                                catch { }
                            }

                            if (w != null)
                            {
                                w.Activate();
                            }

                            if (_dte.ActiveDocument != null)
                            {
                                TextSelection sel = _dte.ActiveDocument.Selection as TextSelection;
                                if (sel != null && line > 0)
                                {
                                    sel.GotoLine(line, true);
                                }
                            }
                            success = true;
                        }
                    }
                }
                catch { }
            });
            return success;
        }

        private void ExecuteOnUI(MethodInvoker action)
        {
            if (_syncControl != null && _syncControl.InvokeRequired)
            {
                _syncControl.Invoke(action);
            }
            else
            {
                action();
            }
        }
    }
}
