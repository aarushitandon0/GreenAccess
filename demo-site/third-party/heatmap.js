/*
 * "Herald Heatmap" -- a fake click/scroll heatmap recorder.
 *
 * Part of demo defect THIRD-PARTY-01. It records coordinates into an in-memory
 * grid and never transmits them.
 */
(function (global) {
  'use strict';

  var GRID = 24;
  var clicks = [];
  var moves = 0;
  var grid = [];

  function resetGrid() {
    grid = [];
    for (var y = 0; y < GRID; y += 1) {
      var row = [];
      for (var x = 0; x < GRID; x += 1) {
        row.push(0);
      }
      grid.push(row);
    }
  }

  function cellFor(clientX, clientY) {
    var w = global.innerWidth || 1;
    var h = global.innerHeight || 1;
    var cx = Math.min(GRID - 1, Math.max(0, Math.floor((clientX / w) * GRID)));
    var cy = Math.min(GRID - 1, Math.max(0, Math.floor((clientY / h) * GRID)));
    return { x: cx, y: cy };
  }

  function selectorFor(element) {
    if (!element || !element.tagName) {
      return '';
    }
    var parts = [];
    var node = element;
    var depth = 0;
    while (node && node.tagName && depth < 4) {
      var part = node.tagName.toLowerCase();
      if (node.id) {
        part += '#' + node.id;
        parts.unshift(part);
        break;
      }
      if (node.className && typeof node.className === 'string') {
        var first = node.className.split(/\s+/)[0];
        if (first) {
          part += '.' + first;
        }
      }
      parts.unshift(part);
      node = node.parentNode;
      depth += 1;
    }
    return parts.join(' > ');
  }

  function onClick(event) {
    var cell = cellFor(event.clientX, event.clientY);
    grid[cell.y][cell.x] += 1;
    clicks.push({
      x: event.clientX,
      y: event.clientY,
      target: selectorFor(event.target),
      at: Date.now()
    });
    if (clicks.length > 400) {
      clicks.shift();
    }
  }

  function onMove() {
    moves += 1;
  }

  resetGrid();

  global.HeraldHeatmap = {
    grid: function () {
      return grid;
    },
    clicks: function () {
      return clicks.slice();
    },
    stats: function () {
      return { clicks: clicks.length, moves: moves, cells: GRID * GRID };
    },
    reset: function () {
      clicks.length = 0;
      moves = 0;
      resetGrid();
    }
  };

  if (global.document) {
    global.document.addEventListener('click', onClick, true);
    global.document.addEventListener('mousemove', onMove, { passive: true });
  }
})(window);
