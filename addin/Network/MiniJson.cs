using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;

namespace VS2005QwenRAG.Network
{
    /// <summary>
    /// Lightweight, zero-dependency JSON parser and serializer for .NET 2.0
    /// </summary>
    public static class MiniJson
    {
        public static object Deserialize(string json)
        {
            if (string.IsNullOrEmpty(json)) return null;
            int index = 0;
            return ParseValue(json, ref index);
        }

        private static object ParseValue(string json, ref int index)
        {
            SkipWhitespace(json, ref index);
            if (index >= json.Length) return null;

            char c = json[index];
            if (c == '{') return ParseObject(json, ref index);
            if (c == '[') return ParseArray(json, ref index);
            if (c == '"') return ParseString(json, ref index);
            if (c == 't' || c == 'T' || c == 'f' || c == 'F') return ParseBoolean(json, ref index);
            if (c == 'n' || c == 'N') return ParseNull(json, ref index);
            if (char.IsDigit(c) || c == '-') return ParseNumber(json, ref index);

            index++;
            return null;
        }

        private static Dictionary<string, object> ParseObject(string json, ref int index)
        {
            Dictionary<string, object> dict = new Dictionary<string, object>();
            index++; // skip '{'

            while (index < json.Length)
            {
                SkipWhitespace(json, ref index);
                if (index >= json.Length) break;
                if (json[index] == '}') { index++; break; }

                if (json[index] == ',') { index++; continue; }

                string key = ParseString(json, ref index);
                SkipWhitespace(json, ref index);
                if (index < json.Length && json[index] == ':') index++;

                object value = ParseValue(json, ref index);
                if (key != null) dict[key] = value;

                SkipWhitespace(json, ref index);
                if (index < json.Length && json[index] == ',') index++;
                else if (index < json.Length && json[index] == '}') { index++; break; }
            }
            return dict;
        }

        private static List<object> ParseArray(string json, ref int index)
        {
            List<object> list = new List<object>();
            index++; // skip '['

            while (index < json.Length)
            {
                SkipWhitespace(json, ref index);
                if (index >= json.Length) break;
                if (json[index] == ']') { index++; break; }

                if (json[index] == ',') { index++; continue; }

                object value = ParseValue(json, ref index);
                list.Add(value);

                SkipWhitespace(json, ref index);
                if (index < json.Length && json[index] == ',') index++;
                else if (index < json.Length && json[index] == ']') { index++; break; }
            }
            return list;
        }

        private static string ParseString(string json, ref int index)
        {
            SkipWhitespace(json, ref index);
            if (index >= json.Length || json[index] != '"') return string.Empty;
            index++; // skip '"'

            StringBuilder sb = new StringBuilder();
            while (index < json.Length)
            {
                char c = json[index++];
                if (c == '"') return sb.ToString();
                if (c == '\\' && index < json.Length)
                {
                    char esc = json[index++];
                    switch (esc)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u':
                            if (index + 4 <= json.Length)
                            {
                                string hex = json.Substring(index, 4);
                                index += 4;
                                try { sb.Append((char)Convert.ToInt32(hex, 16)); }
                                catch { }
                            }
                            break;
                        default: sb.Append(esc); break;
                    }
                }
                else
                {
                    sb.Append(c);
                }
            }
            return sb.ToString();
        }

        private static object ParseNumber(string json, ref int index)
        {
            int start = index;
            if (json[index] == '-') index++;
            while (index < json.Length && (char.IsDigit(json[index]) || json[index] == '.' || json[index] == 'e' || json[index] == 'E' || json[index] == '+'))
            {
                index++;
            }
            string numStr = json.Substring(start, index - start);
            if (numStr.Contains("."))
            {
                double d;
                if (double.TryParse(numStr, System.Globalization.NumberStyles.Any, System.Globalization.CultureInfo.InvariantCulture, out d))
                    return d;
            }
            long l;
            if (long.TryParse(numStr, out l)) return l;
            return 0;
        }

        private static bool ParseBoolean(string json, ref int index)
        {
            if (json.Length >= index + 4 && json.Substring(index, 4).ToLower() == "true")
            {
                index += 4;
                return true;
            }
            if (json.Length >= index + 5 && json.Substring(index, 5).ToLower() == "false")
            {
                index += 5;
                return false;
            }
            return false;
        }

        private static object ParseNull(string json, ref int index)
        {
            if (json.Length >= index + 4 && json.Substring(index, 4).ToLower() == "null")
            {
                index += 4;
            }
            return null;
        }

        private static void SkipWhitespace(string json, ref int index)
        {
            while (index < json.Length && char.IsWhiteSpace(json[index]))
            {
                index++;
            }
        }

        public static string Escape(string text)
        {
            if (text == null) return "";
            StringBuilder sb = new StringBuilder();
            foreach (char c in text)
            {
                switch (c)
                {
                    case '\\': sb.Append("\\\\"); break;
                    case '"': sb.Append("\\\""); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 32) sb.AppendFormat("\\u{0:x4}", (int)c);
                        else sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }
    }
}
