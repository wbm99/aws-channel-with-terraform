// Ad break scheduling for the player, kept out of index.html so it can be tested on its own.
// MediaPackage writes each SCTE-35 break into the playlist as an EXT-X-DATERANGE; hls.js parses those into
// dateRanges, and hls.playingDate says which wall-clock moment of the stream is on screen. These two functions turn
// the first into a list of breaks and say which one, if any, is under the playhead.
(function () {
  // Breaks are the date ranges carrying SCTE35-OUT with a length (DURATION, else PLANNED-DURATION); others are ignored.
  function breaks(dateRanges) {
    return Object.values(dateRanges || {}).flatMap((range) => {
      const seconds = range.duration ?? range.plannedDuration;
      const out = range.attr && range.attr['SCTE35-OUT'] !== undefined;
      if (!out || !(seconds > 0) || !range.startDate) return [];
      const startMs = range.startDate.getTime();
      return [{ id: range.id, startMs, endMs: startMs + seconds * 1000 }];
    });
  }

  // The first break not played yet whose window holds the playhead. A viewer who joins mid-break gets the rest of it.
  function due(list, playingMs, played) {
    return list.find((b) => !played.has(b.id) && b.startMs <= playingMs && playingMs < b.endMs) || null;
  }

  window.AdBreaks = { breaks, due };
})();
