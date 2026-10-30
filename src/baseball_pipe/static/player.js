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
            receiverAppID: 'DB0F4596',
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

   // Namespace for the receiver -> sender debug channel wired into
   // cast-receiver.html -- lets us see what Shaka is actually doing on the
   // TV without relying on chrome://inspect, which has proven unreliable
   // against this device's embedded Chromium build.
   var DEBUG_NAMESPACE = 'urn:x-cast:com.baseballpipe.debug';

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

         // Listen for debug messages pushed back from the receiver
         // (cast-receiver.html) over our own custom message channel.
         var castSession = cast.framework.CastContext.getInstance().getCurrentSession();
         if (castSession) {
            castSession.addMessageListener(DEBUG_NAMESPACE, function(namespace, message) {
               // Log the raw JSON string, not a parsed live object -- a
               // live object only ever prints as an unexpandable "{...}"
               // in a plain-text console export (the same issue BUFFER
               // DEBUG hit earlier), which made every HEARTBEAT/event
               // payload we added unreadable in every exported .log file
               // so far. The string survives copy/export intact.
               console.log('RECEIVER DEBUG:', message);
            });
         } else {
            console.warn('DEBUG CHANNEL: no active cast session to attach listener to.');
         }
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

         // Direct from the raw Cast SDK, bypassing silvermine's abstraction --
         // tells us whether Google's own framework sees an available receiver
         // for our configured app ID at all, independent of whether the
         // plugin decides to show a button for it.
         // Query it directly too -- the change-event alone may never fire
         // if the state has been static since page load (change events
         // typically only fire on an actual transition, not an initial
         // snapshot), so don't rely on it alone.
         console.log('CURRENT CAST STATE:', cast.framework.CastContext.getInstance().getCastState());

         cast.framework.CastContext.getInstance().addEventListener(
            cast.framework.CastContextEventType.CAST_STATE_CHANGED,
            (event) => {
               console.log('CAST STATE CHANGED:', event.castState);
            }
         );
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