// player.js
videojs.log.level('all'); // Enable verbose internal Video.js logging

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
      techOrder: [ 'chromecast', 'html5' ], 
      plugins: {
         chromecast: {
            addButtonToControlBar: true,
         }
      }
   };

   // Initialize the player
   var player = videojs('hls-cast-player', options);

   player.ready(function() {
      console.log('--- DEBUG: Video.js Ready ---');
      
      // 1. Check Plugin Presence
      if (player.chromecast) {
         console.log('SUCCESS: Chromecast plugin attached to player instance.');
      } else {
         console.error('ERROR: Chromecast plugin NOT found on player.');
      }

      // 2. Logging the Handshake (The "Sender" level)
      player.on('chromecastRequested', function() {
         console.log('EVENT: Cast button clicked. Requesting session from Google SDK...');
      });

      player.on('chromecastConnected', function() {
         console.log('EVENT: Connected to Chromecast! Handing over the URL...');
         // Log the source being sent to the TV
         console.log('SOURCE SHIPPED TO TV:', player.currentSrc());
      });

      player.on('chromecastDisconnected', function() {
         console.warn('EVENT: Chromecast disconnected.');
      });

      // 3. The "Dealbreaker" Logs
      player.on('chromecastError', function(event) {
         console.error('SILVERMINE ERROR:', event.error);
         // This will often catch things like 'CANCEL' or 'LOAD_FAILED'
         if (event.error === 'LOAD_FAILED') {
            console.error('DIAGNOSTIC: The TV rejected the HLS manifest. Check HTTPS and CORS.');
         }
      });

      // 4. Tech Change Log
      player.on('usingcustomcontrols', function(e, isCustom) {
          // Silvermine uses custom controls when casting
          console.log('TECH CHANGE: Is using custom/chromecast controls?', isCustom);
      });
   });

   // Global Catch for the Google SDK itself
   window.__onGCastApiAvailable = function(isAvailable) {
      if (isAvailable) {
         console.log('GOOGLE SDK: Cast API is available on the window.');
      } else {
         console.error('GOOGLE SDK: Cast API failed to initialize.');
      }
   };
});