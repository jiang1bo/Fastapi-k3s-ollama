/* 缓存页面控件引用，避免每次事件都重复查找 DOM。 */
const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const chatBox = document.getElementById("chat-box");
const sendButton = document.getElementById("send-button");
const modelSelect = document.getElementById("model-select");
const statusText = document.getElementById("status");

/* 仅保存已经成功完成的往返消息，发送失败时不污染后续上下文。 */
const history = [];

/**
 * 安全地把一条对话消息插入页面，并滚动到最新消息。
 *
 * @param {"user"|"assistant"} role 消息角色。
 * @param {string} content 纯文本消息内容。
 */
function addMessage(role, content) {
    const article = document.createElement("article");
    article.className = `message ${role}`;

    const badge = document.createElement("strong");
    badge.className = "role";
    badge.textContent = role === "user" ? "我" : "AI";

    const text = document.createElement("div");
    text.className = "content";
    // 使用 textContent，绝不把模型生成文本作为 HTML 执行。
    text.textContent = content;

    article.append(badge, text);
    chatBox.appendChild(article);
    chatBox.scrollTop = chatBox.scrollHeight;
}

/**
 * 读取后端模型白名单和安装状态，再开放聊天控件。
 */
async function loadModels() {
    try {
        const response = await fetch("/api/models");
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || "读取模型目录失败");
        }

        modelSelect.replaceChildren();
        const available = result.models.filter((model) => model.available);
        for (const model of result.models) {
            const option = document.createElement("option");
            option.value = model.id;
            option.textContent = model.available
                ? model.label
                : `${model.label}（未安装）`;
            option.disabled = !model.available;
            modelSelect.appendChild(option);
        }

        if (available.length === 0) {
            throw new Error("Ollama 中尚未安装任何配置的模型");
        }

        modelSelect.value = available[0].id;
        modelSelect.disabled = false;
        input.disabled = false;
        sendButton.disabled = false;
        statusText.textContent = `已连接 Ollama，可用模型：${available.length}`;
    } catch (error) {
        // 保持控件禁用，避免用户向未就绪服务发送推理请求。
        statusText.textContent = `模型服务不可用：${error.message}`;
    }
}

/* 页面打开时获取模型列表；服务端返回当前 PVC 中实际已安装的模型。 */
loadModels();

/**
 * 发送聊天请求；记录 Ollama token 用量和生成速度供实验者查看。
 */
form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || !modelSelect.value) {
        return;
    }

    addMessage("user", message);
    input.disabled = true;
    sendButton.disabled = true;
    modelSelect.disabled = true;
    statusText.textContent = "模型正在 CPU 上推理，请稍候...";

    try {
        const response = await fetch("/api/chat", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                message,
                model: modelSelect.value,
                // 仅传已完成历史；后端会再次按配置限制消息数。
                history: history.slice(-10),
            }),
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || "聊天请求失败");
        }

        addMessage("assistant", result.answer);
        history.push(
            {role: "user", content: message},
            {role: "assistant", content: result.answer},
        );

        const prompt = result.prompt_tokens ?? "未知";
        const generated = result.generated_tokens ?? "未知";
        const speed = result.generation_tokens_per_second;
        const speedText = speed === null ? "未知" : `${speed.toFixed(2)} token/s`;
        const duration = result.total_duration_seconds;
        const durationText = duration === null ? "未知" : `${duration.toFixed(2)} 秒`;
        statusText.textContent =
            `${result.model} · 输入 ${prompt} tokens · 输出 ${generated} tokens · ` +
            `${speedText} · 总耗时 ${durationText}`;
    } catch (error) {
        addMessage("assistant", `请求失败：${error.message}`);
        statusText.textContent = `推理失败：${error.message}`;
    } finally {
        input.disabled = false;
        sendButton.disabled = false;
        modelSelect.disabled = false;
        input.value = "";
        input.focus();
    }
});

/* Enter 发送消息，Shift+Enter 保留换行输入行为。 */
input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
    }
});
