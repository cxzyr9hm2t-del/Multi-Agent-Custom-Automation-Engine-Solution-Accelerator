// ============================================================================
// Module: Azure Container App (AVM)
// AVM Module: avm/res/app/container-app:0.22.1
// ============================================================================

@description('Name of the container app.')
param name string

@description('Azure region for deployment.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Resource ID of the Container Apps Environment.')
param environmentResourceId string

@description('Container definitions.')
param containers array

@description('Enable external ingress.')
param ingressExternal bool = true

@description('Target port for ingress.')
param ingressTargetPort int = 80

@description('Ingress transport protocol.')
@allowed(['auto', 'http', 'http2', 'tcp'])
param ingressTransport string = 'auto'

@description('Whether to allow insecure ingress connections.')
param ingressAllowInsecure bool = false

@description('Disable ingress entirely (for background workers).')
param disableIngress bool = false

@description('Container registry configurations.')
param registries array?

@description('Secret definitions.')
param secrets array?

@description('Managed identity configuration.')
param managedIdentities object = {}

@description('CORS policy configuration.')
param corsPolicy object = {}

@description('Active revision mode.')
@allowed(['Single', 'Multiple'])
param activeRevisionsMode string = 'Single'

@description('Scale settings (maxReplicas, minReplicas, rules, cooldownPeriod, pollingInterval).')
param scaleSettings object = {
  maxReplicas: 10
  minReplicas: 0
}

@description('Workload profile name.')
param workloadProfileName string?

@description('Enable Azure telemetry collection.')
param enableTelemetry bool = true

@allowed([
  'none'
  'sticky'
])
@description('Optional. Bool indicating if the Container App should enable session affinity.')
param stickySessionsAffinity string = 'none'

@description('Optional. Entra application (client) ID of the API app registration. When set, the container app is placed behind Container Apps authentication and unauthenticated callers receive a 401. Empty (the default) leaves the app open, which is the pre-existing behaviour.')
param authClientId string = ''

@description('Optional. Entra tenant ID used to validate tokens. Defaults to the deployment tenant.')
param authTenantId string = tenant().tenantId

// ============================================================================
// Container App (AVM)
// ============================================================================
module containerApp 'br/public:avm/res/app/container-app:0.22.1' = {
  name: take('avm.res.app.containerapp.${name}', 64)
  params: {
    name: name
    location: location
    tags: tags
    enableTelemetry: enableTelemetry
    environmentResourceId: environmentResourceId
    containers: containers
    ingressExternal: disableIngress ? false : ingressExternal
    ingressTargetPort: ingressTargetPort
    ingressTransport: ingressTransport
    ingressAllowInsecure: ingressAllowInsecure
    disableIngress: disableIngress
    registries: registries
    secrets: secrets
    managedIdentities: !empty(managedIdentities) ? managedIdentities : {}
    corsPolicy: !empty(corsPolicy) ? corsPolicy : null
    activeRevisionsMode: activeRevisionsMode
    scaleSettings: scaleSettings
    workloadProfileName: workloadProfileName
    stickySessionsAffinity: stickySessionsAffinity
    // Only configured when an app registration is supplied; otherwise the app
    // stays open, which is the pre-existing behaviour. `Return401` rejects
    // unauthenticated callers rather than issuing a browser login redirect,
    // which is what an API wants.
    authConfig: empty(authClientId) ? {} : {
      platform: {
        enabled: true
      }
      globalValidation: {
        unauthenticatedClientAction: 'Return401'
      }
      identityProviders: {
        azureActiveDirectory: {
          enabled: true
          registration: {
            openIdIssuer: 'https://login.microsoftonline.com/${authTenantId}/v2.0'
            clientId: authClientId
          }
          validation: {
            allowedAudiences: [
              'api://${authClientId}'
              authClientId
            ]
          }
        }
      }
    }
  }
}

// ============================================================================
// Outputs
// ============================================================================
@description('The name of the container app.')
output name string = containerApp.outputs.name

@description('The resource ID of the container app.')
output resourceId string = containerApp.outputs.resourceId

@description('The FQDN of the container app.')
output fqdn string = containerApp.outputs.fqdn

@description('System-assigned identity principal ID.')
output principalId string = containerApp.outputs.?systemAssignedMIPrincipalId ?? ''
