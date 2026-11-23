using System;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Collections.Generic;
using VS2005QwenRAG.Core;

namespace VS2005QwenRAG.Network
{
    public delegate void HealthCallback(bool isOnline);
    public delegate void IndexCallback(IndexResult result);
    public delegate void QueryCallback(QueryResult result);
    public delegate void ModelSwitchCallback(bool success, string message);
    public delegate void MapStatusCallback(bool ok, string state, int mappedFiles, int totalFiles, double percent, string message);

    /// <summary>
    /// Thread-safe HTTP Client for communication with local Python backend.
    /// Provides purely asynchronous methods to guarantee no UI hangs.
    /// </summary>
    public class BackendClient
    {
        private string _baseUrl;

        public BackendClient(string baseUrl)
        {
            _baseUrl = baseUrl.TrimEnd('/');
        }

        public string BaseUrl
        {
            get { return _baseUrl; }
            set { _baseUrl = value.TrimEnd('/'); }
        }

        public void CheckHealthAsync(HealthCallback callback)
        {
            ThreadPool.QueueUserWorkItem(delegate(object state)
            {
                bool online = false;
                try
                {
                    HttpWebRequest req = (HttpWebRequest)WebRequest.Create(_baseUrl + "/api/health");
                    req.Timeout = 2500;
                    req.Method = "GET";
                    using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                    {
                        online = (resp.StatusCode == HttpStatusCode.OK);
                    }
                }
                catch
                {
                    online = false;
                }

                if (callback != null)
                {
                    callback(online);
                }
            });
        }

        public void IndexDirectoryAsync(string directoryPath, IndexCallback callback)
        {
            ThreadPool.QueueUserWorkItem(delegate(object state)
            {
                IndexResult result = new IndexResult();
                try
                {
                    string payload = string.Format("{{\"path\":\"{0}\"}}", MiniJson.Escape(directoryPath));
                    string responseText = SendPostRequest(_baseUrl + "/api/index", payload, 180000);

                    Dictionary<string, object> dict = MiniJson.Deserialize(responseText) as Dictionary<string, object>;
                    if (dict != null)
                    {
                        result.Success = dict.ContainsKey("success") && Convert.ToBoolean(dict["success"]);
                        if (dict.ContainsKey("files_count")) result.FilesCount = Convert.ToInt32(dict["files_count"]);
                        if (dict.ContainsKey("symbols_count")) result.SymbolsCount = Convert.ToInt32(dict["symbols_count"]);
                        if (dict.ContainsKey("calls_count")) result.CallsCount = Convert.ToInt32(dict["calls_count"]);
                        if (dict.ContainsKey("duration_seconds")) result.DurationSeconds = Convert.ToDouble(dict["duration_seconds"]);
                        if (dict.ContainsKey("message")) result.Message = Convert.ToString(dict["message"]);
                    }
                }
                catch (Exception ex)
                {
                    result.Success = false;
                    result.Message = ex.Message;
                }

                if (callback != null)
                {
                    callback(result);
                }
            });
        }

        public void AskAsync(string question, string mode, string currentFile, int currentLine, string selectedCode, QueryCallback callback)
        {
            AskAsync(question, mode, currentFile, currentLine, selectedCode, "ru", callback);
        }

        public void AskAsync(string question, string mode, string currentFile, int currentLine, string selectedCode, string language, QueryCallback callback)
        {
            ThreadPool.QueueUserWorkItem(delegate(object state)
            {
                QueryResult result = new QueryResult();
                try
                {
                    StringBuilder sb = new StringBuilder();
                    sb.Append("{");
                    sb.AppendFormat("\"question\":\"{0}\",", MiniJson.Escape(question));
                    sb.AppendFormat("\"mode\":\"{0}\",", MiniJson.Escape(mode));
                    sb.AppendFormat("\"current_file\":\"{0}\",", MiniJson.Escape(currentFile ?? ""));
                    sb.AppendFormat("\"current_line\":{0},", currentLine);
                    sb.AppendFormat("\"selected_code\":\"{0}\",", MiniJson.Escape(selectedCode ?? ""));
                    sb.AppendFormat("\"language\":\"{0}\"", MiniJson.Escape(language ?? "ru"));
                    sb.Append("}");

                    string responseText = SendPostRequest(_baseUrl + "/api/query", sb.ToString(), 780000);
                    Dictionary<string, object> dict = MiniJson.Deserialize(responseText) as Dictionary<string, object>;
                    if (dict != null)
                    {
                        result.Success = dict.ContainsKey("success") && Convert.ToBoolean(dict["success"]);
                        if (dict.ContainsKey("answer")) result.Answer = Convert.ToString(dict["answer"]);
                        if (dict.ContainsKey("error")) result.Error = Convert.ToString(dict["error"]);

                        if (dict.ContainsKey("citations"))
                        {
                            List<object> citList = dict["citations"] as List<object>;
                            if (citList != null)
                            {
                                foreach (object item in citList)
                                {
                                    Dictionary<string, object> citDict = item as Dictionary<string, object>;
                                    if (citDict != null)
                                    {
                                        CodeCitation cit = new CodeCitation();
                                        if (citDict.ContainsKey("file")) cit.FilePath = Convert.ToString(citDict["file"]);
                                        if (citDict.ContainsKey("line")) cit.Line = Convert.ToInt32(citDict["line"]);
                                        if (citDict.ContainsKey("symbol")) cit.Symbol = Convert.ToString(citDict["symbol"]);
                                        if (citDict.ContainsKey("snippet")) cit.Snippet = Convert.ToString(citDict["snippet"]);
                                        result.Citations.Add(cit);
                                    }
                                }
                            }
                        }
                    }
                    else
                    {
                        result.Success = false;
                        result.Answer = responseText;
                    }
                }
                catch (Exception ex)
                {
                    result.Success = false;
                    result.Error = ex.Message;
                }

                if (callback != null)
                {
                    callback(result);
                }
            });
        }

        public void SelectModelAsync(string modelPath, ModelSwitchCallback callback)
        {
            ThreadPool.QueueUserWorkItem(delegate(object state)
            {
                bool ok = false;
                string msg = "";
                try
                {
                    string payload = string.Format("{{\"model_path\":\"{0}\",\"restart_server\":true}}", MiniJson.Escape(modelPath));
                    string responseText = SendPostRequest(_baseUrl + "/api/models/select", payload, 60000);
                    Dictionary<string, object> dict = MiniJson.Deserialize(responseText) as Dictionary<string, object>;
                    if (dict != null)
                    {
                        ok = dict.ContainsKey("success") && Convert.ToBoolean(dict["success"]);
                        if (dict.ContainsKey("message")) msg = Convert.ToString(dict["message"]);
                    }
                }
                catch (Exception ex)
                {
                    ok = false;
                    msg = ex.Message;
                }

                if (callback != null)
                {
                    callback(ok, msg);
                }
            });
        }

        public void StartMapBuildAsync(MapStatusCallback callback)
        {
            ThreadPool.QueueUserWorkItem(delegate(object stateObj)
            {
                bool ok = false;
                string st = "idle";
                int mapped = 0;
                int total = 0;
                double pct = 0.0;
                string msg = "";
                try
                {
                    string responseText = SendPostRequest(_baseUrl + "/api/map/build", "{\"force_rebuild\":false}", 15000);
                    Dictionary<string, object> dict = MiniJson.Deserialize(responseText) as Dictionary<string, object>;
                    if (dict != null)
                    {
                        ok = true;
                        if (dict.ContainsKey("message")) msg = Convert.ToString(dict["message"]);
                        Dictionary<string, object> statusDict = dict.ContainsKey("status") ? (dict["status"] as Dictionary<string, object>) : null;
                        if (statusDict != null)
                        {
                            if (statusDict.ContainsKey("state")) st = Convert.ToString(statusDict["state"]);
                            if (statusDict.ContainsKey("mapped_files")) mapped = Convert.ToInt32(statusDict["mapped_files"]);
                            if (statusDict.ContainsKey("total_files")) total = Convert.ToInt32(statusDict["total_files"]);
                            if (statusDict.ContainsKey("percent")) pct = Convert.ToDouble(statusDict["percent"]);
                        }
                    }
                }
                catch (Exception ex)
                {
                    ok = false;
                    msg = ex.Message;
                }

                if (callback != null)
                {
                    callback(ok, st, mapped, total, pct, msg);
                }
            });
        }

        public void CancelMapBuildAsync(MapStatusCallback callback)
        {
            ThreadPool.QueueUserWorkItem(delegate(object stateObj)
            {
                bool ok = false;
                string st = "cancelled";
                int mapped = 0;
                int total = 0;
                double pct = 0.0;
                string msg = "";
                try
                {
                    string responseText = SendPostRequest(_baseUrl + "/api/map/cancel", "{}", 10000);
                    Dictionary<string, object> dict = MiniJson.Deserialize(responseText) as Dictionary<string, object>;
                    if (dict != null)
                    {
                        ok = true;
                        if (dict.ContainsKey("message")) msg = Convert.ToString(dict["message"]);
                    }
                }
                catch (Exception ex)
                {
                    ok = false;
                    msg = ex.Message;
                }

                if (callback != null)
                {
                    callback(ok, st, mapped, total, pct, msg);
                }
            });
        }

        public void GetMapStatusAsync(MapStatusCallback callback)
        {
            ThreadPool.QueueUserWorkItem(delegate(object stateObj)
            {
                bool ok = false;
                string st = "idle";
                int mapped = 0;
                int total = 0;
                double pct = 0.0;
                string msg = "";
                try
                {
                    HttpWebRequest req = (HttpWebRequest)WebRequest.Create(_baseUrl + "/api/map/status");
                    req.Timeout = 4000;
                    req.Method = "GET";
                    string responseText;
                    using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                    {
                        using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                        {
                            responseText = sr.ReadToEnd();
                        }
                    }
                    Dictionary<string, object> dict = MiniJson.Deserialize(responseText) as Dictionary<string, object>;
                    if (dict != null)
                    {
                        ok = true;
                        if (dict.ContainsKey("state")) st = Convert.ToString(dict["state"]);
                        if (dict.ContainsKey("mapped_files")) mapped = Convert.ToInt32(dict["mapped_files"]);
                        if (dict.ContainsKey("total_files")) total = Convert.ToInt32(dict["total_files"]);
                        if (dict.ContainsKey("percent")) pct = Convert.ToDouble(dict["percent"]);
                        if (dict.ContainsKey("current_file")) msg = Convert.ToString(dict["current_file"]);
                    }
                }
                catch (Exception ex)
                {
                    ok = false;
                    msg = ex.Message;
                }

                if (callback != null)
                {
                    callback(ok, st, mapped, total, pct, msg);
                }
            });
        }

        private string SendPostRequest(string url, string jsonBody, int timeoutMs)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = "POST";
            req.ContentType = "application/json; charset=utf-8";
            req.Timeout = timeoutMs;
            req.ReadWriteTimeout = timeoutMs;

            byte[] bytes = Encoding.UTF8.GetBytes(jsonBody);
            req.ContentLength = bytes.Length;

            using (Stream os = req.GetRequestStream())
            {
                os.Write(bytes, 0, bytes.Length);
            }

            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            {
                using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    return sr.ReadToEnd();
                }
            }
        }
    }
}
