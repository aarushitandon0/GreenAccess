/*
 * "Herald AdTech" -- a fake ad slot filler.
 *
 * Part of demo defect THIRD-PARTY-01. Inert: it defines slots and measures
 * them, but never requests or renders a real advertisement.
 */
(function (global) {
  'use strict';

  var SLOT_SELECTOR = '[data-ad-slot]';
  var slots = [];
  var refreshTimer = null;

  function Slot(element, index) {
    this.element = element;
    this.index = index;
    this.id = 'herald-slot-' + index;
    this.sizes = [
      [970, 250],
      [728, 90],
      [300, 250]
    ];
    this.filled = false;
    this.impressions = 0;
  }

  Slot.prototype.viewable = function () {
    if (!this.element || !this.element.getBoundingClientRect) {
      return false;
    }
    var box = this.element.getBoundingClientRect();
    var height = global.innerHeight || 0;
    return box.top < height && box.bottom > 0;
  };

  Slot.prototype.fill = function () {
    if (this.filled) {
      return false;
    }
    this.filled = true;
    this.impressions += 1;
    this.element.setAttribute('data-ad-state', 'filled');
    return true;
  };

  function discover() {
    if (!global.document) {
      return;
    }
    var found = global.document.querySelectorAll(SLOT_SELECTOR);
    for (var i = 0; i < found.length; i += 1) {
      slots.push(new Slot(found[i], i));
    }
  }

  function sweep() {
    for (var i = 0; i < slots.length; i += 1) {
      if (slots[i].viewable()) {
        slots[i].fill();
      }
    }
  }

  function targeting() {
    var doc = global.document;
    return {
      path: global.location ? global.location.pathname : '/',
      referrer: doc ? doc.referrer : '',
      keywords: ['news', 'local', 'politics', 'culture'],
      viewport: (global.innerWidth || 0) + 'x' + (global.innerHeight || 0)
    };
  }

  global.HeraldAds = {
    slots: slots,
    targeting: targeting,
    refresh: function () {
      sweep();
      return slots.length;
    },
    start: function () {
      discover();
      sweep();
      if (!refreshTimer) {
        refreshTimer = global.setInterval(sweep, 30000);
      }
    },
    stop: function () {
      if (refreshTimer) {
        global.clearInterval(refreshTimer);
        refreshTimer = null;
      }
    }
  };

  if (global.document) {
    if (global.document.readyState === 'loading') {
      global.document.addEventListener('DOMContentLoaded', global.HeraldAds.start);
    } else {
      global.HeraldAds.start();
    }
  }
})(window);
