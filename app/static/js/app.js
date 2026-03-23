const SCENE_EDITOR_SELECTOR = "form[data-scene-editor]";
const RISKY_LOGO_PATTERN = /(清晰|清楚|明顯|可辨識|顯眼|醒目|visible|clear(?:ly)?\s+visible|recognizable).{0,8}(logo|標誌|商標|招牌)|\b(logo|logos|trademark|brand mark|wordmark)\b|品牌標誌|企業標誌|商標字樣|招牌字樣/i;
const RISKY_BRAND_PATTERN = /(apple|google|meta|tesla|microsoft|amazon|openai|nvidia|nike|adidas|starbucks|coca-?cola|mcdonald'?s|samsung|sony|intel|amd|asus|acer|mediat(?:e|e)k|foxconn|tsmc|鴻海|富士康|台積電|蘋果|谷歌|微軟|亞馬遜|特斯拉|輝達|耐吉|愛迪達|星巴克|可口可樂|麥當勞|三星|索尼|英特爾|華碩|宏碁|聯發科)/i;

function detectVideoPromptRisks(text) {
    const normalizedText = text.trim();
    if (!normalizedText) {
        return [];
    }

    const risks = [];
    if (RISKY_BRAND_PATTERN.test(normalizedText)) {
        risks.push("提到可能可辨識的真實品牌或公司名稱");
    }
    if (RISKY_LOGO_PATTERN.test(normalizedText)) {
        risks.push("提到清楚 logo、商標或品牌標誌");
    }

    return risks;
}

function countNarrationCharacters(text) {
    return Array.from(text.trim()).length;
}

function formatSeconds(seconds) {
    return seconds.toFixed(1).replace(/\.0$/, "");
}

function updateSceneEditorState(form) {
    const narrationInput = form.querySelector('textarea[name="narration_text"]');
    const videoPromptInput = form.querySelector('textarea[name="video_prompt"]');
    const feedback = form.querySelector("[data-narration-feedback]");
    const limitMessage = form.querySelector("[data-narration-over-limit]");
    const riskFeedback = form.querySelector("[data-video-prompt-risk-feedback]");
    const saveButton = form.querySelector("[data-save-prompt-button]");
    if (!narrationInput || !videoPromptInput || !feedback || !limitMessage || !riskFeedback || !saveButton) {
        return;
    }

    const maxChars = Number.parseInt(form.dataset.maxNarrationChars ?? "0", 10);
    const maxSeconds = Number.parseFloat(form.dataset.maxSceneSeconds ?? "0");
    const charsPerSecond = Number.parseFloat(form.dataset.charsPerSecond ?? "1");
    const currentChars = countNarrationCharacters(narrationInput.value);
    const estimatedSeconds = currentChars / charsPerSecond;
    const isOverLimit = currentChars > maxChars || estimatedSeconds > maxSeconds;
    const isNearLimit = !isOverLimit && currentChars >= Math.floor(maxChars * 0.85);

    feedback.textContent = `目前字數：${currentChars} 字，預估旁白時長：約 ${formatSeconds(estimatedSeconds)} 秒 / 上限 ${formatSeconds(maxSeconds)} 秒（建議不超過 ${maxChars} 字）`;
    feedback.classList.toggle("is-warning", isNearLimit);
    feedback.classList.toggle("is-error", isOverLimit);
    narrationInput.classList.toggle("is-invalid", isOverLimit);
    limitMessage.hidden = !isOverLimit;
    saveButton.disabled = isOverLimit;

    const videoPromptRisks = detectVideoPromptRisks(videoPromptInput.value);
    const hasVideoPromptRisk = videoPromptRisks.length > 0;
    videoPromptInput.classList.toggle("is-warning", hasVideoPromptRisk);
    riskFeedback.hidden = !hasVideoPromptRisk;
    if (hasVideoPromptRisk) {
        riskFeedback.textContent = `偵測到高風險描述：${videoPromptRisks.join("、")}。這類內容可能觸發影片模型 moderation，建議改成泛化場景，例如「大型科技公司總部」或「不出現可辨識 logo」。`;
    } else {
        riskFeedback.textContent = "";
    }

    if (isOverLimit) {
        narrationInput.setCustomValidity(
            `旁白預估約 ${formatSeconds(estimatedSeconds)} 秒，已超過 ${formatSeconds(maxSeconds)} 秒上限。請縮短後再儲存。`
        );
    } else {
        narrationInput.setCustomValidity("");
    }
}

function initializeSceneEditors(root = document) {
    root.querySelectorAll(SCENE_EDITOR_SELECTOR).forEach((form) => {
        updateSceneEditorState(form);
    });
}

document.body.addEventListener("input", (event) => {
    const form = event.target.closest(SCENE_EDITOR_SELECTOR);
    if (!form || !["narration_text", "video_prompt"].includes(event.target.name)) {
        return;
    }

    updateSceneEditorState(form);
});

document.body.addEventListener(
    "submit",
    (event) => {
        const form = event.target.closest(SCENE_EDITOR_SELECTOR);
        if (!form) {
            return;
        }

        updateSceneEditorState(form);
        const narrationInput = form.querySelector('textarea[name="narration_text"]');
        if (narrationInput && !narrationInput.checkValidity()) {
            event.preventDefault();
            narrationInput.reportValidity();
            return;
        }

        const videoPromptInput = form.querySelector('textarea[name="video_prompt"]');
        if (!videoPromptInput) {
            return;
        }

        const videoPromptRisks = detectVideoPromptRisks(videoPromptInput.value);
        if (videoPromptRisks.length === 0) {
            return;
        }

        const confirmationMessage = [
            "目前影片提示詞包含可能觸發 moderation 的高風險描述：",
            ...videoPromptRisks.map((risk) => `- ${risk}`),
            "",
            "建議改成泛化描述，避免真實品牌名稱與清楚 logo。仍要繼續儲存嗎？",
        ].join("\n");

        if (!window.confirm(confirmationMessage)) {
            event.preventDefault();
        }
    },
    true,
);

// 提供 HTMX 所需的最小化前端增強
document.body.addEventListener("htmx:afterSwap", (event) => {
    // 視需要捲動到新加入的內容
    if (event.detail.target.id === "project-list") {
        const firstChild = event.detail.target.firstElementChild;
        if (firstChild) {
            firstChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
    }

    initializeSceneEditors(event.detail.target);
});

initializeSceneEditors();
