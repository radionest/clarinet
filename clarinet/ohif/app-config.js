/** @type {AppTypes.Config} */
window.config = {
  routerBasename: '/ohif',
  showStudyList: false,
  extensions: [],
  modes: [],
  maxNumberOfWebWorkers: 3,
  showLoadingIndicator: true,
  strictZSpacingForVolumeViewport: true,
  customizationService: {
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
  },
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
