document.addEventListener('DOMContentLoaded', async () => {
  console.log("Initializing Shaka UI overlay...");

  shaka.polyfill.installAll();

  if (!shaka.Player.isBrowserSupported()) {
    console.error("Browser not supported!");
    return;
  }

  const video = document.getElementById('video');
  const container = document.getElementById('video-container');

  // Create ONE UI overlay
  const ui = new shaka.ui.Overlay(
    new shaka.Player(video),
    container,
    video
  );

  const controls = ui.getControls();
  const player = controls.getPlayer();

  try {
    await player.load(window.m3u8_url);
    console.log("Shaka loaded stream:", window.m3u8_url);
  } catch (err) {
    console.error("Shaka load error:", err);
  }
});
