import {
  AlertCircle,
  CheckCircle,
  FileJson,
  KeyRound,
  Loader2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import api from "../api";

// 与 /creds/upload 的文件数量上限一致；每条 RT 对应一个凭证文件。
const MAX_IMPORT_COUNT = 100;

/** 凭证导入页：接收文件或逐行 RT，统一提交并展示逐条导入结果。 */
export default function UploadPage() {
  const [mode, setMode] = useState("files");
  const [rtInput, setRtInput] = useState("");
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  // 输入为逐行 RT 文本；输出为按首次出现顺序去重的 RT 和重复条数。
  const { tokens, duplicateCount } = useMemo(() => {
    const lines = rtInput
      .split(/\r\n|\n|\r/)
      .map((line) => line.trim())
      .filter(Boolean);
    const uniqueTokens = [...new Set(lines)];
    return {
      tokens: uniqueTokens,
      duplicateCount: lines.length - uniqueTokens.length,
    };
  }, [rtInput]);
  const tooManyTokens = tokens.length > MAX_IMPORT_COUNT;

  /** 接收待选文件，合并支持的 JSON / ZIP；同名文件只保留一份。 */
  const addFiles = (fileList) => {
    if (uploading) return;
    const valid = Array.from(fileList).filter(
      (f) => f.name.endsWith(".json") || f.name.endsWith(".zip")
    );
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [
        ...prev,
        ...valid.filter((f) => {
          if (names.has(f.name)) return false;
          names.add(f.name);
          return true;
        }),
      ];
    });
    setResults(null);
  };

  /** 按文件名移除待上传文件。 */
  const removeFile = (name) => {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  };

  /** 将传入的 File 数组提交至现有导入接口，异步更新结果与忙碌状态。 */
  const upload = async (uploadFiles) => {
    if (uploading || uploadFiles.length === 0) return;
    if (uploadFiles.length > MAX_IMPORT_COUNT) {
      setResults({
        error: `单次最多导入 ${MAX_IMPORT_COUNT} 个文件，请分批导入`,
      });
      return;
    }
    setUploading(true);
    setResults(null);

    try {
      const formData = new FormData();
      uploadFiles.forEach((f) => formData.append("files", f));

      const res = await api.post("/creds/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setResults(res.data);
    } catch (err) {
      setResults({
        error: err.response?.data?.detail || "导入失败，请重试",
      });
    } finally {
      setUploading(false);
    }
  };

  /** 将已去重的 RT 转为最小凭证 JSON 文件，复用上传流程；文件名仅包含序号。 */
  const importTokens = () => {
    if (uploading || tokens.length === 0 || tooManyTokens) return;
    const tokenFiles = tokens.map(
      (token, index) =>
        new File(
          [JSON.stringify({ refresh_token: token })],
          `rt-${String(index + 1).padStart(3, "0")}.json`,
          { type: "application/json" }
        )
    );
    return upload(tokenFiles);
  };

  const successCount = results?.uploaded_count || 0;
  const totalCount = results?.total_count || 0;
  const hasFailures = totalCount > successCount;
  const allFailed = totalCount > 0 && successCount === 0;

  return (
    <div className="space-y-6">
      <div className="bg-gradient-to-br from-emerald-900/30 to-emerald-800/10 border border-emerald-700/30 rounded-xl p-5">
        <h3 className="text-lg font-semibold text-emerald-400 mb-2">
          批量导入凭证
        </h3>
        <p className="text-sm text-gray-400">
          支持上传 JSON / ZIP 凭证文件，或直接粘贴 Antigravity
          RT，系统会自动获取缺失的邮箱、项目信息和订阅等级。
        </p>
        {mode === "files" && (
          <div className="mt-3 text-xs text-gray-500 bg-dark-800/60 rounded-lg p-3">
            <p className="mb-1 font-medium text-gray-400">规范 JSON 格式：</p>
            <pre className="text-emerald-300/80 whitespace-pre-wrap font-mono">{`{
  "refresh_token": "必填",
  "access_token": "可选，未过期时直接复用",
  "expiry": "可选，access_token 过期时间",
  "email": "可选，缺失自动获取",
  "tier": "可选，缺失自动获取",
  "project_id": "可选，缺失自动获取"
}`}</pre>
            <p className="mt-2">
              仅填 refresh_token 即可导入；同邮箱凭证自动去重（覆盖更新）。
            </p>
          </div>
        )}
      </div>

      {/* 导入方式切换：保留各自输入，切换时清除上一种方式的结果。 */}
      <div
        className="inline-flex w-full sm:w-auto gap-1 rounded-xl border border-dark-700 bg-dark-900 p-1"
        role="group"
        aria-label="导入方式"
      >
        {[
          { id: "files", label: "文件导入", icon: FileJson },
          { id: "rt", label: "RT 导入", icon: KeyRound },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            aria-pressed={mode === id}
            disabled={uploading}
            onClick={() => {
              setMode(id);
              setResults(null);
              setDragOver(false);
            }}
            className={`flex flex-1 sm:flex-none items-center justify-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium transition-colors disabled:opacity-50 ${
              mode === id
                ? "bg-purple-600 text-white"
                : "text-gray-400 hover:bg-dark-800 hover:text-gray-200"
            }`}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </div>

      {/* RT 导入：逐行输入，实时显示去重条数及上限提示。 */}
      {mode === "rt" ? (
        <div className="card space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="rounded-xl border border-purple-500/20 bg-purple-500/10 p-3 text-purple-400">
                <KeyRound size={22} />
              </div>
              <div>
                <label
                  htmlFor="rt-input"
                  className="font-semibold text-gray-200"
                >
                  粘贴 Refresh Token
                </label>
                <p id="rt-description" className="mt-1 text-xs text-gray-400">
                  每行一条 RT，无需填写邮箱或 Project ID
                </p>
              </div>
            </div>
            <span
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                tooManyTokens
                  ? "bg-red-500/10 text-red-400"
                  : "bg-purple-500/10 text-purple-300"
              }`}
            >
              {tokens.length} 条待导入
            </span>
          </div>
          <div className="space-y-2">
            <textarea
              id="rt-input"
              value={rtInput}
              onChange={(e) => {
                setRtInput(e.target.value);
                setResults(null);
              }}
              disabled={uploading}
              rows={8}
              spellCheck={false}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              aria-describedby="rt-description rt-limit"
              aria-invalid={tooManyTokens}
              placeholder={"在此粘贴 Antigravity RT\n多条 RT 请换行分隔"}
              className={`w-full resize-y rounded-xl border bg-dark-950/60 px-4 py-3 font-mono text-sm leading-6 text-gray-200 placeholder-gray-600 transition-colors disabled:opacity-50 ${
                tooManyTokens
                  ? "border-red-500/50"
                  : "border-dark-600 hover:border-dark-500"
              }`}
            />
            <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs">
              <span className="text-gray-500">
                {duplicateCount > 0
                  ? `已忽略 ${duplicateCount} 条重复 RT`
                  : "自动忽略空行和首尾空白，并去除重复 RT"}
              </span>
              <span
                id="rt-limit"
                className={tooManyTokens ? "text-red-400" : "text-gray-500"}
              >
                {tooManyTokens
                  ? `超出上限 ${tokens.length - MAX_IMPORT_COUNT} 条，请分批导入`
                  : `单次最多 ${MAX_IMPORT_COUNT} 条`}
              </span>
            </div>
          </div>
          <div className="flex flex-col gap-4 border-t border-dark-700 pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs leading-5 text-gray-500">
              导入时自动校验 RT，同邮箱凭证覆盖更新。
            </p>
            <div className="flex w-full gap-2 sm:w-auto">
              <button
                type="button"
                onClick={() => {
                  setRtInput("");
                  setResults(null);
                }}
                disabled={uploading || !rtInput}
                className="btn btn-secondary flex items-center justify-center gap-1.5 text-sm disabled:opacity-50"
              >
                <Trash2 size={14} /> 清空
              </button>
              <button
                type="button"
                onClick={importTokens}
                disabled={uploading || tokens.length === 0 || tooManyTokens}
                className="btn btn-primary flex flex-1 items-center justify-center gap-2 whitespace-nowrap text-sm sm:flex-none disabled:opacity-50"
              >
                {uploading ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <Upload size={16} />
                )}
                {uploading ? "导入中..." : "导入 RT"}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* 上传区域 */}
          <div
            className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors ${
              dragOver
                ? "border-purple-500 bg-purple-500/10"
                : "border-dark-600 hover:border-dark-500"
            }`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              addFiles(e.dataTransfer.files);
            }}
          >
            <Upload className="mx-auto text-purple-400 mb-3" size={32} />
            <p className="text-gray-300">点击选择文件或拖拽文件到此区域</p>
            <p className="text-gray-500 text-sm mt-1">
              支持 .json 和 .zip 格式，ZIP 会自动提取其中的 JSON 凭证
            </p>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".json,.zip"
              disabled={uploading}
              className="hidden"
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>

          {/* 文件列表 */}
          {files.length > 0 && (
            <div className="card space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-gray-300">
                  已选择 {files.length} 个文件
                </h4>
                <div className="flex gap-2">
                  <button
                    onClick={() => upload(files)}
                    disabled={uploading}
                    className="btn btn-primary text-sm flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {uploading ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Upload size={14} />
                    )}
                    {uploading ? "上传中..." : "上传文件"}
                  </button>
                  <button
                    onClick={() => setFiles([])}
                    disabled={uploading}
                    className="btn btn-secondary text-sm disabled:opacity-50"
                  >
                    清空列表
                  </button>
                </div>
              </div>
              <div className="space-y-1.5 max-h-64 overflow-y-auto">
                {files.map((f) => (
                  <div
                    key={f.name}
                    className="flex items-center justify-between bg-dark-800 border border-dark-600 rounded-lg px-3 py-2 text-sm"
                  >
                    <div className="flex items-center gap-2 text-gray-300 min-w-0">
                      <FileJson
                        size={15}
                        className="text-purple-400 flex-shrink-0"
                      />
                      <span className="truncate">{f.name}</span>
                    </div>
                    <div className="flex items-center gap-3 flex-shrink-0">
                      <span className="text-gray-500 text-xs">
                        {(f.size / 1024).toFixed(1)} KB
                      </span>
                      <button
                        onClick={() => removeFile(f.name)}
                        disabled={uploading}
                        aria-label={`移除 ${f.name}`}
                        className="text-gray-500 hover:text-red-400 disabled:opacity-50"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* 文件与 RT 共用结果展示，区分全部成功、部分失败和全部失败。 */}
      {results && (
        <div className="card space-y-3" role="status" aria-live="polite">
          {results.error ? (
            <div className="bg-red-500/10 border border-red-500/30 text-red-400 px-4 py-3 rounded-lg">
              {results.error}
            </div>
          ) : (
            <>
              <div
                className={`px-4 py-3 rounded-lg border flex items-center gap-2 text-sm ${
                  allFailed
                    ? "bg-red-500/10 border-red-500/30 text-red-400"
                    : hasFailures
                      ? "bg-amber-500/10 border-amber-500/30 text-amber-400"
                      : "bg-green-500/10 border-green-500/30 text-green-400"
                }`}
              >
                {hasFailures ? (
                  <AlertCircle size={16} className="flex-shrink-0" />
                ) : (
                  <CheckCircle size={16} className="flex-shrink-0" />
                )}
                {results.message ||
                  `导入完成: 成功 ${successCount}/${totalCount} 个凭证`}
              </div>
              {results.results?.some((r) => r.status === "error") && (
                <div className="space-y-1.5">
                  <div className="text-sm text-gray-400">失败详情:</div>
                  {results.results
                    .filter((r) => r.status === "error")
                    .map((r, i) => (
                      <div
                        key={i}
                        className="bg-red-500/10 border border-red-500/20 text-red-400 text-xs px-3 py-2 rounded-lg break-words"
                      >
                        <span className="font-medium">{r.filename}</span>:{" "}
                        {r.message}
                      </div>
                    ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
