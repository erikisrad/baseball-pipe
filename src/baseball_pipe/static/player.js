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
      techOrder: ['chromecast', 'html5'],
      plugins: {
         chromecast: {
            addButtonToControlBar: true,
         }
      }
   };

   // Initialize the player
   var player = videojs('hls-cast-player', options);

   // videojs.log.level('all') alone doesn't unlock VHS's internal debug
   // logger (it's gated behind log.debug, not log.level) -- player.debug(true)
   // is the call that actually flips it on, surfacing granular VHS internals
   // like buffer-stall counts and exclusion reasons leading up to a rendition switch
   player.debug(true);

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

   // Force HTML5 tech to initialize track menus
    const src = player.currentSrc();
    // Force HTML5 to load the playlist first
    player.src({
        src: src,
        type: 'application/x-mpegURL'
    });

    // 5. Buffer instrumentation -- logs the *actual* buffered() TimeRanges on
    // every segment append, so we can see the real numbers behind VHS's
    // PlaybackWatcher.checkSegmentDownloads_ (it excludes a playlist forever
    // once its buffered() snapshot is byte-identical across 10 straight
    // appendsdone checks -- we need the real start/end values to know why it
    // isn't changing, since the console's plain-text export only shows
    // "buffered: Array(1)" with no expanded contents).
    function attachBufferLogging() {
        const tech = player.tech({ IWillNotUseThisInPlugins: true });
        const vhs = tech && tech.vhs;
        const mpc = vhs && vhs.masterPlaylistController_;
        const mainLoader = mpc && mpc.mainSegmentLoader_;
        const sourceUpdater = mpc && mpc.sourceUpdater_;
        if (!mainLoader) {
            return false;
        }

        // ranges is flattened to a plain string, not a nested array --
        // DevTools' plain-text console export collapses nested
        // objects/arrays to "Array(1)" with no expandable contents,
        // which is exactly what made the first capture attempt useless
        function rangesToString(buffered) {
            const ranges = [];
            for (let i = 0; i < buffered.length; i++) {
                ranges.push(buffered.start(i).toFixed(3) + '-' + buffered.end(i).toFixed(3));
            }
            return ranges.join(', ');
        }

        mainLoader.on('appendsdone', function() {
            try {
                console.log('BUFFER DEBUG:', {
                    currentTime: player.currentTime(),
                    playlist: mainLoader.playlist_ && mainLoader.playlist_.id,
                    video: sourceUpdater && sourceUpdater.videoBuffer ? rangesToString(sourceUpdater.videoBuffer.buffered) : 'n/a',
                    audio: sourceUpdater && sourceUpdater.audioBuffer ? rangesToString(sourceUpdater.audioBuffer.buffered) : 'n/a'
                });
            } catch (e) {
                console.error('BUFFER DEBUG: logging failed', e);
            }
        });

        console.log('BUFFER DEBUG: attached to mainSegmentLoader_');
        return true;
    }

    // player.src() above tears down and rebuilds the tech, so the segment
    // loader we want doesn't exist yet at this point -- retry a few times
    // rather than assuming any single event fires after it's ready.
    let attachAttempts = 0;
    const attachInterval = setInterval(function() {
        attachAttempts++;
        if (attachBufferLogging() || attachAttempts >= 20) {
            clearInterval(attachInterval);
        }
    }, 250);

});