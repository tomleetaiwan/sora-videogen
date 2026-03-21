// 提供 HTMX 所需的最小化前端增強
document.body.addEventListener("htmx:afterSwap", (event) => {
    // 視需要捲動到新加入的內容
    if (event.detail.target.id === "project-list") {
        const firstChild = event.detail.target.firstElementChild;
        if (firstChild) {
            firstChild.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
    }
});
