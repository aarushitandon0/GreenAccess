/*
 * "Herald Analytics" -- a fake first-party-looking analytics tag.
 *
 * Part of demo defect THIRD-PARTY-01. Served from localhost:8082, a different
 * host from the page itself, so the scanner classifies it as third party.
 * It collects nothing and sends nothing anywhere; every "beacon" is a no-op.
 */
(function (global) {
  'use strict';

  var ENDPOINT = 'http://localhost:8082/collect';
  var SESSION_KEY = 'herald_sid';
  var buffer = [];

  function uuid() {
    var out = '';
    var chars = '0123456789abcdef';
    for (var i = 0; i < 32; i += 1) {
      out += chars.charAt(Math.floor(Math.random() * 16));
      if (i === 7 || i === 11 || i === 15 || i === 19) {
        out += '-';
      }
    }
    return out;
  }

  function sessionId() {
    try {
      var existing = global.localStorage.getItem(SESSION_KEY);
      if (existing) {
        return existing;
      }
      var fresh = uuid();
      global.localStorage.setItem(SESSION_KEY, fresh);
      return fresh;
    } catch (error) {
      return uuid();
    }
  }

  function environment() {
    var screen = global.screen || {};
    return {
      ua: global.navigator ? global.navigator.userAgent : '',
      lang: global.navigator ? global.navigator.language : '',
      screen: (screen.width || 0) + 'x' + (screen.height || 0),
      depth: screen.colorDepth || 0,
      tz: new Date().getTimezoneOffset(),
      dpr: global.devicePixelRatio || 1,
      touch: 'ontouchstart' in global,
      cookies: global.navigator ? global.navigator.cookieEnabled : false
    };
  }

  function flush() {
    if (buffer.length === 0) {
      return;
    }
    // Deliberately inert: the demo never makes a real network call here.
    var payload = { sid: sessionId(), env: environment(), events: buffer.slice() };
    buffer.length = 0;
    if (global.console && global.console.debug) {
      global.console.debug('[herald-analytics] would POST to', ENDPOINT, payload.events.length);
    }
  }

  var HeraldAnalytics = {
    queue: buffer,
    push: function (record) {
      buffer.push(record);
      if (buffer.length >= 12) {
        flush();
      }
      return buffer.length;
    },
    pageview: function () {
      return HeraldAnalytics.push({ event: 'pageview', at: Date.now() });
    },
    identify: function (traits) {
      return HeraldAnalytics.push({ event: 'identify', traits: traits || {}, at: Date.now() });
    },
    flush: flush
  };

  global.HeraldAnalytics = HeraldAnalytics;

  if (global.document) {
    global.document.addEventListener('visibilitychange', function () {
      if (global.document.visibilityState === 'hidden') {
        flush();
      }
    });
  }

  HeraldAnalytics.pageview();
})(window);
