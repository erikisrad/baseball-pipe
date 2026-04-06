// player-setup.js
document.addEventListener('DOMContentLoaded', function() {
   
   // 1. Manually initialize the chromecast plugin UI 
   // (This is often required for the Silvermine plugin to inject the button)
   if (window.videojs && window.videojs.getComponent('ChromecastButton')) {
      // Logic for older versions or specific builds if needed
   }

   var options = {
      fluid: true,
      responsive: true,
      // Use standard techOrder; the plugin handles the 'casting' tech internally
      techOrder: [ 'html5' ], 
      plugins: {
         chromecast: {
            addButtonToControlBar: true,
            // Optional: You can specify a receiver ID here if you had a custom one
            // receiverAppID: 'CC1AD845' 
         }
      }
   };

   // Initialize the player
   var player = videojs('hls-cast-player', options);

   player.ready(function() {
      console.log('Video.js is ready!');
      
      // Double-check if the plugin is actually loaded
      if (!player.chromecast) {
         console.error('Chromecast plugin failed to load. Check your script paths.');
      }
   });
});