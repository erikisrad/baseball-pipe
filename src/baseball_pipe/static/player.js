// player.js

document.addEventListener('DOMContentLoaded', function() {

   var options = {
      fluid: true,
      responsive: true,
      liveui: true,
      liveTracker: {
         trackingThreshold: 5,
         liveTolerance: 5
      },
      techOrder: ['chromecast', 'html5'],
      plugins: {
         chromecast: {
            addButtonToControlBar: true,
            receiverAppID: 'DB0F4596',
         }
      }
   };

   var player = videojs('hls-cast-player', options);

   const src = player.currentSrc();
   player.src({
      src: src,
      type: 'application/x-mpegURL'
   });

});
