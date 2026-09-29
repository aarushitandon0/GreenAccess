/*
 * The Daily Herald -- demo behaviour.
 *
 * DELIBERATELY DEFECTIVE. See demo-site/DEFECTS.md.
 *
 * Planted defects in this file:
 *   TRAP-01         the promo widget traps Tab and has no Escape handler
 *   DIV-BUTTON-01   the div "buttons" are wired with onclick only
 *   CHAT-CLOSE-01   the chat close control has no accessible name
 *   UNCOMPRESSED-01 this file is served without gzip or brotli on purpose
 *
 * Written in ES5 style with no bundler, which is what a site like this would
 * actually ship.
 */

(function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   * Defect TRAP-01
   *
   * Once focus reaches any control inside #promo, this handler cancels every
   * Tab press and cycles focus between the widget's three controls. Focus can
   * never leave, and Escape does nothing. A keyboard-only visitor who tabs
   * this far is stuck on the page for good.
   *
   * MASTERSPEC §6.4 defines a trap as the same cycle of <= 6 elements
   * repeating for >= 3 full cycles while focusable elements remain outside it.
   * This cycle is 3 elements long, and the page has roughly 28 focusable
   * elements ahead of it, so the Tab crawl should see it clearly.
   * ------------------------------------------------------------------ */

  function installPromoTrap() {
    var promo = document.getElementById('promo');
    if (!promo) {
      return;
    }

    var trapped = [
      document.getElementById('promo-yes'),
      document.getElementById('promo-no'),
      document.getElementById('promo-terms')
    ].filter(Boolean);

    if (trapped.length === 0) {
      return;
    }

    document.addEventListener(
      'keydown',
      function (event) {
        if (event.key !== 'Tab' && event.keyCode !== 9) {
          return;
        }

        var index = trapped.indexOf(document.activeElement);
        if (index === -1) {
          // Focus is still elsewhere on the page; let it travel normally
          // until it arrives here.
          return;
        }

        event.preventDefault();

        var next = event.shiftKey ? index - 1 : index + 1;
        if (next < 0) {
          next = trapped.length - 1;
        }
        if (next >= trapped.length) {
          next = 0;
        }
        trapped[next].focus();
      },
      true
    );

    // Note the absence of an Escape handler, and of any close control.
  }

  /* ------------------------------------------------------------------ *
   * Fake buttons (defect DIV-BUTTON-01)
   *
   * These are reachable with a mouse only. No role, no tabindex, no keyboard
   * activation.
   * ------------------------------------------------------------------ */

  window.loadMore = function (direction) {
    var grid = document.querySelector('.card-grid');
    if (!grid) {
      return;
    }
    grid.setAttribute('data-page-direction', String(direction));
    track('pagination', { direction: direction });
  };

  window.openPaywall = function () {
    var promo = document.getElementById('promo');
    if (promo) {
      promo.style.display = 'block';
    }
    track('paywall_open', {});
  };

  window.filterBy = function (tag) {
    var tags = document.querySelectorAll('.tag');
    for (var i = 0; i < tags.length; i += 1) {
      tags[i].removeAttribute('data-active');
    }
    track('filter', { tag: tag });
  };

  window.subscribe = function (event) {
    if (event && event.preventDefault) {
      event.preventDefault();
    }
    var form = document.querySelector('.newsletter__form');
    if (form) {
      // No validation message is surfaced to assistive technology.
      form.setAttribute('data-submitted', 'true');
    }
    track('newsletter_submit', {});
    return false;
  };

  /* Defect CHAT-CLOSE-01: the control that calls this has no accessible name. */
  window.closeChat = function () {
    var chat = document.getElementById('chat');
    if (chat) {
      chat.style.display = 'none';
    }
    track('chat_close', {});
  };

  /* ------------------------------------------------------------------ *
   * A small in-page analytics shim, on top of the four third-party
   * trackers loaded in the document head.
   * ------------------------------------------------------------------ */

  var queue = [];

  function track(name, payload) {
    var record = {
      event: name,
      payload: payload || {},
      at: Date.now(),
      path: window.location.pathname,
      referrer: document.referrer,
      viewport: window.innerWidth + 'x' + window.innerHeight
    };
    queue.push(record);
    if (window.HeraldAnalytics && typeof window.HeraldAnalytics.push === 'function') {
      window.HeraldAnalytics.push(record);
    }
  }

  function trackScrollDepth() {
    var marks = [25, 50, 75, 100];
    var seen = {};
    window.addEventListener(
      'scroll',
      function () {
        var doc = document.documentElement;
        var height = doc.scrollHeight - doc.clientHeight;
        if (height <= 0) {
          return;
        }
        var percent = Math.round((doc.scrollTop / height) * 100);
        for (var i = 0; i < marks.length; i += 1) {
          if (percent >= marks[i] && !seen[marks[i]]) {
            seen[marks[i]] = true;
            track('scroll_depth', { depth: marks[i] });
          }
        }
      },
      { passive: true }
    );
  }

  function trackReadingTime() {
    var started = Date.now();
    window.addEventListener('beforeunload', function () {
      track('reading_time', { seconds: Math.round((Date.now() - started) / 1000) });
    });
  }

  function decorateCards() {
    var cards = document.querySelectorAll('.card');
    for (var i = 0; i < cards.length; i += 1) {
      cards[i].setAttribute('data-card-index', String(i));
    }
  }

  function start() {
    installPromoTrap();
    trackScrollDepth();
    trackReadingTime();
    decorateCards();
    track('page_view', { title: document.title });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
