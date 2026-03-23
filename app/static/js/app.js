const SCENE_EDITOR_SELECTOR = "form[data-scene-editor]";

function countNarrationCharacters(text) {
    return Array.from(text.trim()).length;
}

function formatSeconds(seconds) {
    return seconds.toFixed(1).replace(/\.0$/, "");
}

function updateSceneEditorState(form) {
    const narrationInput = form.querySelector('textarea[name="narration_text"]');
    const feedback = form.querySelector("[data-narration-feedback]");
    const limitMessage = form.querySelector("[data-narration-over-limit]");
    const saveButton = form.querySelector("[data-save-prompt-button]");
    if (!narrationInput || !feedback || !limitMessage || !saveButton) {
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
    if (!form || event.target.name !== "narration_text") {
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
