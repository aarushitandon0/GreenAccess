/*
 * "Herald Social" -- a fake share-button widget.
 *
 * Part of demo defect THIRD-PARTY-01. It builds share URLs but never opens or
 * requests anything.
 */
(function (global) {
  'use strict';

  var NETWORKS = {
    bluesky: 'https://example.invalid/share/bluesky?url=',
    mastodon: 'https://example.invalid/share/mastodon?url=',
    email: 'mailto:?body=',
    copy: ''
  };

  function currentUrl() {
    return global.location ? global.location.href : '';
  }

  function shareUrl(network) {
    var base = NETWORKS[network];
    if (base === undefined) {
      return '';
    }
    if (network === 'copy') {
      return currentUrl();
    }
    return base + encodeURIComponent(currentUrl());
  }

  function counts() {
    // A real widget would fetch these. The demo fabricates stable numbers so
    // the page stays deterministic.
    return { bluesky: 128, mastodon: 64, email: 12, copy: 0 };
  }

  function mount(container) {
    if (!container || !global.document) {
      return null;
    }
    var wrapper = global.document.createElement('div');
    wrapper.className = 'herald-share';
    var names = Object.keys(NETWORKS);
    for (var i = 0; i < names.length; i += 1) {
      var button = global.document.createElement('span');
      button.className = 'herald-share__btn';
      button.setAttribute('data-network', names[i]);
      // No accessible name, no real button element -- consistent with the rest
      // of this deliberately broken page.
      wrapper.appendChild(button);
    }
    container.appendChild(wrapper);
    return wrapper;
  }

  global.HeraldSocial = {
    networks: Object.keys(NETWORKS),
    shareUrl: shareUrl,
    counts: counts,
    mount: mount
  };
})(window);
