/** @type {AppTypes.Config} */
window.config = {
  routerBasename: '/ohif',
  showStudyList: false,
  extensions: [],
  modes: [],
  maxNumberOfWebWorkers: 3,
  showLoadingIndicator: true,
  strictZSpacingForVolumeViewport: true,
  customizationService: [{
    'viewportOverlay.topRight': [
      {
        id: 'PatientNameOverlay',
        customizationType: 'ohif.overlayItem',
        attribute: 'PatientName',
        label: '',
        title: 'Patient Name',
        condition: ({ instance }) => instance?.PatientName,
        contentF: ({ instance, formatters: { formatPN } }) =>
          formatPN(instance.PatientName),
      },
      {
        id: 'PatientIDOverlay',
        customizationType: 'ohif.overlayItem',
        attribute: 'PatientID',
        label: 'ID:',
        title: 'Patient ID',
        condition: ({ instance }) => instance?.PatientID,
      },
    ],
    'studyBrowser.studyMode': {$set: 'primary'},
  }],
  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'dicomweb',
      configuration: {
        friendlyName: 'Clarinet PACS',
        name: 'clarinet',
        wadoUriRoot: '/dicom-web',
        qidoRoot: '/dicom-web',
        wadoRoot: '/dicom-web',
        qidoSupportsIncludeField: false,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
        supportsFuzzyMatching: false,
        supportsWildcard: false,
      },
    },
  ],
  defaultDataSourceName: 'dicomweb',
};

// Override default mouse bindings: left click = StackScroll, right click = WindowLevel.
// OHIF v3.12 hardcodes bindings in modes/basic/src/initToolGroups.ts and does not read
// them from customizationService for the default tool group, so we re-bind at runtime
// after the toolGroupService has been initialized and tools have been added.
(function patchOhifMouseBindings() {
  // Numeric values of cornerstone3D MouseBindings enum, pinned to @cornerstonejs/tools
  // 4.15.29 (bundled by @ohif/extension-cornerstone 3.12.0 — see OHIF v3.12.0
  // extensions/cornerstone/package.json). Source of truth:
  // https://github.com/cornerstonejs/cornerstone3D/blob/v4.15.29/packages/tools/src/enums/ToolBindings.ts
  // If clarinet/settings.py:ohif_default_version is bumped past 3.12.x, re-verify
  // these constants against the new bundled cornerstone3D version — a silent enum
  // shift would make the rebind apply to the wrong mouse buttons.
  var Primary = 1;
  var Secondary = 2;
  var Wheel = 524288;

  var TARGET_GROUPS = ['default', 'mpr', 'SRToolGroup'];

  function rebind(toolGroup) {
    try {
      // 1. Move WindowLevel from Primary to Secondary.
      toolGroup.setToolPassive('WindowLevel');
      toolGroup.setToolActive('WindowLevel', {
        bindings: [{ mouseButton: Secondary }],
      });
      // 2. Drop Zoom mouse binding (touch pinch via numTouchPoints stays).
      toolGroup.setToolPassive('Zoom');
      toolGroup.setToolActive('Zoom', { bindings: [{ numTouchPoints: 2 }] });
      // 3. Bind StackScroll to Primary (Wheel/touch bindings already added by basic mode).
      toolGroup.setToolActive('StackScroll', {
        bindings: [
          { mouseButton: Primary },
          { mouseButton: Wheel },
          { numTouchPoints: 3 },
        ],
      });
    } catch (err) {
      console.warn('[clarinet] failed to rebind OHIF mouse tools', err);
    }
  }

  function tryAttach() {
    var services = window.services;
    var tgs = services && services.toolGroupService;
    if (!tgs || !tgs.EVENTS || !tgs.subscribe) {
      return false;
    }
    tgs.subscribe(tgs.EVENTS.TOOLGROUP_CREATED, function (evt) {
      var id = evt && evt.toolGroupId;
      if (TARGET_GROUPS.indexOf(id) === -1) {
        return;
      }
      // TOOLGROUP_CREATED fires before addToolsToToolGroup runs in the same call
      // stack (see ToolGroupService.createToolGroupAndAddTools), so defer to a
      // microtask to apply our rebind AFTER the basic mode's hardcoded bindings.
      Promise.resolve().then(function () {
        var group = tgs.getToolGroup(id);
        if (group) {
          rebind(group);
        }
      });
    });
    return true;
  }

  // window.services is assigned during cornerstone extension init, which runs
  // after app-config.js. Poll until it appears, then attach and stop polling.
  if (tryAttach()) {
    return;
  }
  var iv = setInterval(function () {
    if (tryAttach()) {
      clearInterval(iv);
    }
  }, 50);
  // Safety stop after 30s so we don't poll forever in degraded environments.
  setTimeout(function () {
    clearInterval(iv);
  }, 30000);
})();
