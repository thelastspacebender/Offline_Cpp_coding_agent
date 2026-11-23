using System;
using System.IO;
using System.Collections.Generic;

namespace VS2005QwenRAG.Core
{
    public class CodeCitation
    {
        private string _filePath;
        private int _line;
        private string _symbol;
        private string _snippet;

        public string FilePath
        {
            get { return _filePath; }
            set { _filePath = value; }
        }

        public int Line
        {
            get { return _line; }
            set { _line = value; }
        }

        public string Symbol
        {
            get { return _symbol; }
            set { _symbol = value; }
        }

        public string Snippet
        {
            get { return _snippet; }
            set { _snippet = value; }
        }

        public string FileName
        {
            get
            {
                if (string.IsNullOrEmpty(_filePath)) return string.Empty;
                return Path.GetFileName(_filePath);
            }
        }

        public override string ToString()
        {
            return string.Format("{0}:{1} - {2}", FileName, Line, Symbol);
        }
    }

    public class QueryResult
    {
        private bool _success;
        private string _answer;
        private string _error;
        private List<CodeCitation> _citations;

        public bool Success
        {
            get { return _success; }
            set { _success = value; }
        }

        public string Answer
        {
            get { return _answer; }
            set { _answer = value; }
        }

        public string Error
        {
            get { return _error; }
            set { _error = value; }
        }

        public List<CodeCitation> Citations
        {
            get { return _citations; }
            set { _citations = value; }
        }

        public QueryResult()
        {
            _citations = new List<CodeCitation>();
        }
    }

    public class IndexResult
    {
        private bool _success;
        private int _filesCount;
        private int _symbolsCount;
        private int _callsCount;
        private double _durationSeconds;
        private string _message;

        public bool Success
        {
            get { return _success; }
            set { _success = value; }
        }

        public int FilesCount
        {
            get { return _filesCount; }
            set { _filesCount = value; }
        }

        public int SymbolsCount
        {
            get { return _symbolsCount; }
            set { _symbolsCount = value; }
        }

        public int CallsCount
        {
            get { return _callsCount; }
            set { _callsCount = value; }
        }

        public double DurationSeconds
        {
            get { return _durationSeconds; }
            set { _durationSeconds = value; }
        }

        public string Message
        {
            get { return _message; }
            set { _message = value; }
        }
    }
}
