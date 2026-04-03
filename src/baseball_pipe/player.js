// player-setup.js
document.addEventListener('DOMContentLoaded', function() {
   
  var options = {
    fluid: true,               // This makes the player responsive
    responsive: true,          // Helps with mobile resizing
    techOrder: [ 'chromecast', 'html5' ],
    plugins: {
        chromecast: {
          addButtonToControlBar: true
        }
    }
  };

   // Initialize the player
   var player = videojs('hls-cast-player', options);

   player.ready(function() {
      console.log('Video.js is ready with Chromecast support!');
   });
});