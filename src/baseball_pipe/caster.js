// Initialize Chromecast
window.castFramework = cast.framework;
const context = cast.framework.CastContext.getInstance();

context.setOptions({
  receiverApplicationId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
  autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED
});

function castStream() {
  const src = window.m3u8_url;

  const mediaInfo = new chrome.cast.media.MediaInfo(src, 'application/x-mpegURL');
  const request = new chrome.cast.media.LoadRequest(mediaInfo);

  const session = cast.framework.CastContext.getInstance().getCurrentSession();
  if (session) {
    session.loadMedia(request).then(
      () => console.log("Casting started"),
      (err) => console.error("Cast load error:", err)
    );
  }
}