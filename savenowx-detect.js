// Runs ONLY on savenowx.com (see manifest.json's separate
// content_scripts entry for this file). Its only job is to leave a
// marker in the page's DOM so the website's own JS can detect the
// extension is installed and adjust what it shows -- this file does
// nothing else, and injects no visible UI.
document.documentElement.setAttribute("data-savenowx-ext-installed", "true");
