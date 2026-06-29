document.getElementById("tz").value = Intl.DateTimeFormat().resolvedOptions().timeZone;
if (new URLSearchParams(window.location.search).get("error")) {
    const msg = document.getElementById("error-msg");
    msg.style.opacity = "1";
    setTimeout(() => msg.style.opacity = "0", 500);
}
