(function () {
  function addHomeButton() {
    var header = document.querySelector(".md-header__inner");
    if (!header) return;

    if (document.querySelector(".docs-home-button")) return;

    var container = document.createElement("div");
    container.className = "docs-home-button";

    var link = document.createElement("a");
    link.className = "md-button md-button--primary";
    link.href = "/tools";
    link.textContent = "Launch Smart Tools";

    container.appendChild(link);
    header.appendChild(container);
  }

  document.addEventListener("DOMContentLoaded", addHomeButton);
})();
