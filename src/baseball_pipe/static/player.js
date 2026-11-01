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
      // Note: Silvermine often needs 'chromecast' in the techOrder to
      // properly hand off the HLS source to the TV hardware.
      techOrder: ['chromecast', 'html5'],
      plugins: {
         chromecast: {
            addButtonToControlBar: true,
            receiverAppID: 'DB0F4596',
         }
      }
   };

   // Initialize the player
   var player = videojs('hls-cast-player', options);

   // Force HTML5 tech to initialize track menus
   const src = player.currentSrc();
   // Force HTML5 to load the playlist first
   player.src({
      src: src,
      type: 'application/x-mpegURL'
   });

});
