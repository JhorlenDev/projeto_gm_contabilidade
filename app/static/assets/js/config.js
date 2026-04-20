window.GM_CONFIG = {
  debug: false,
  keycloak: {
    realmUrl: "http://35.247.195.232/realms/GM",
    issuer: "http://35.247.195.232/realms/GM",
    clientId: "gm-contabilidade",
    requiredRole: "USER-GM",
  },
  apiBaseUrl: "/api",
  pages: {
    login: "/",
    panel: "/painel/",
  },
};
