// start.js
// Loaded last, once every page has registered: opens the page the window
// was asked for (?page=...), Home by default.

'use strict';

// A failed setup-status call still leaves the hub usable with an actionable
// toast. A replay query deliberately reopens the guide after completion.
(async () => {
  let page = new URLSearchParams(location.search).get('page') || 'home';
  try {
    const status = await Hub.api('setup_status');
    if (!status.completed) page = 'onboarding';
  } catch (error) { Hub.toastError(error); }
  Hub.start(page);
})();
