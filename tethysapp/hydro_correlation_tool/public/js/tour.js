/*
 * Guided first-visit tour (driver.js).
 *
 * ✏️  ALL TOUR COPY LIVES IN THE `steps` ARRAY BELOW — edit the `title` and
 *     `description` strings freely; they accept plain HTML (<b>, <br>, etc.).
 *     Nothing outside the array needs to change to reword the tour.
 *
 * To add a step: copy any block and set `element` to a CSS selector for the
 * thing to spotlight. Omit `element` entirely for a centered "modal" step.
 * `side` controls where the bubble sits relative to the element.
 *
 * The tour auto-starts once per browser (localStorage flag) and can be
 * replayed from the "Take the tour" button in the help modal.
 */
(function () {
  "use strict";

  var TOUR_SEEN_KEY = "hct-tour-seen";

  /* ------------------------------------------------------------------ */
  /* ✏️  TOUR COPY — edit everything between here and END TOUR COPY      */
  /* ------------------------------------------------------------------ */
  var steps = [
    {
      // No `element` → centered welcome dialog.
      popover: {
        popoverClass: "tour-welcome", // wider styling — see main.css
        title: "Welcome to the Hydro-Correlation Tool!",
        description:
          "This tool is a single-user app that allows a researcher to build and verify a table of corresponding NWM, USGS, and GEOGLOWS IDs. Curious about why this table is needed? Click the ? Help button to learn more.",
      },
    },
    {
      element: ".toggle-nav",
      popover: {
        title: "Workflow",
        description:
        "Click here to open the instructions panel, which walks through the workflow.",
        side: "bottom",
      },
    },
    {
      element: ".map-row .col-8",
      popover: {
        title: "The map",
        description:
          "Each dot represents a USGS gage. Upon zooming in, you will see blue lines that represent a stream network — either the NWM or GEOGLOWS.",
        side: "right",
      },
    },
    {
      element: "#tour-network",
      popover: {
        title: "Network toggle",
        description:
          "Here, you can toggle between the NWM and the GEOGLOWS networks. Only one shows at a time.",
        side: "left",
      },
    },
    {
      element: ".headwater-toggle",
      popover: {
        title: "Headwater toggle",
        description:
          "This is the headwater toggle. The smallest tributaries are off by default, but can be added back by clicking here. Check the app settings to configure this filter.",
        side: "left",
      },
    },
    {
      element: "#tour-chart-mode",
      popover: {
        title: "Chart mode",
        description:
          "Data from the stream networks and gages can be shown as a single overlaid chart or separate stacked charts with a shared date axis.",
        side: "left",
      },
    },
    {
      element: "#tour-units",
      popover: {
        title: "Units",
        description:
          "To change units, use these buttons.",
        side: "left",
      },
    },
    {
      element: ".panel-content",
      popover: {
        title: "Gage details",
        description:
          "Metadata and hydrographs end up here after you click a gage. Use them to compare USGS observations with each network's simulated flow.",
        side: "left",
      },
    },
    {
      element: ".header-button",
      popover: {
        title: "Need help?",
        description:
          "If you are having trouble, the help button has additional information. Thanks!",
        side: "bottom",
      },
    },
  ];
  /* ------------------------------------------------------------------ */
  /* END TOUR COPY                                                       */
  /* ------------------------------------------------------------------ */

  function markSeen() {
    try {
      localStorage.setItem(TOUR_SEEN_KEY, "1");
    } catch (e) {
      /* private-browsing mode etc. — fine, tour just reshows next visit */
    }
  }

  function startTour() {
    var driver = window.driver.js.driver({
      showProgress: true,
      progressText: "{{current}} of {{total}}",
      nextBtnText: "Next →",
      prevBtnText: "← Back",
      doneBtnText: "Done",
      steps: steps,
      onDestroyed: markSeen, // fires on Done, X, and Esc alike
    });
    driver.drive();
  }

  // Replay hook — the help modal's "Take the tour" button calls this.
  // Delay slightly so Bootstrap's modal fade-out (and its backdrop) finish
  // before the tour overlay appears; otherwise the two overlays stack.
  window.HCT_START_TOUR = function () {
    window.setTimeout(startTour, 300);
  };

  // Auto-start on first visit only.
  document.addEventListener("DOMContentLoaded", function () {
    var seen = null;
    try {
      seen = localStorage.getItem(TOUR_SEEN_KEY);
    } catch (e) {
      seen = "1"; // can't read storage → don't risk showing it every load
    }
    if (!seen) {
      // Small delay so the map and panel have painted before we spotlight.
      window.setTimeout(startTour, 600);
    }
  });
})();
