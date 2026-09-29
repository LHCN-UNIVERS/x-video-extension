const input = document.getElementById("url-input");
const openBtn = document.getElementById("open-btn");

// Pre-fill from the current tab if it's already a status page --
// activeTab permission only grants this because the user just
// clicked the toolbar icon to open this popup, nothing broader.
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const url = tabs[0]?.url ?? "";
  if (/^https?:\/\/(x|twitter)\.com\/[^/]+\/status\/\d+/.test(url)) {
    input.value = url;
  }
});

function openInSaveNowX() {
  const raw = input.value.trim();
  const match = raw.match(/(x|twitter)\.com\/([^/]+)\/status\/(\d+)/);
  if (!match) {
    input.style.borderColor = "#ff5cb3";
    return;
  }
  const [, , username, id] = match;
  chrome.tabs.create({ url: `https://savenowx.com/${username}/status/${id}` });
}

openBtn.addEventListener("click", openInSaveNowX);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") openInSaveNowX();
});
