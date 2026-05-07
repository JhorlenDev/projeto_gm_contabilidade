window.GM_CONFIG = {
  debug: false,
  keycloak: {
    realmUrl: "http://191.252.181.8/realms/GM",
    issuer: "http://191.252.181.8/realms/GM",
    clientId: "gm-contabilidade",
    requiredRole: "USER-GM",
  },
  apiBaseUrl: "/api",
  pages: {
    login: "/",
    panel: "/painel/",
  },
};
