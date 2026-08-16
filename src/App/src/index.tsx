import React, { StrictMode, useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals';
import { FluentProvider, teamsLightTheme, teamsDarkTheme } from "@fluentui/react-components";
import { setEnvData, setApiUrl, config as defaultConfig, toBoolean, getUserInfo, setUserInfoGlobal, setImageToken } from './api/config';
import { apiService } from './api';
import { Provider as ReduxProvider } from 'react-redux';
import { store } from './store/store';
const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement);

/**
 * Pull an access token for the backend API out of the App Service token store.
 *
 * When the backend is behind Container Apps authentication it expects a bearer
 * token for its own audience (see docs/backend_api_authentication.md). App
 * Service EasyAuth exposes one at /.auth/me once the frontend registration has
 * been granted the API's scope. Silent no-op when the endpoint is absent, so
 * deployments without a front door are unaffected.
 */
async function acquireApiAccessToken(): Promise<void> {
  if (localStorage.getItem('token')) return;
  try {
    const response = await fetch('/.auth/me', { credentials: 'include' });
    if (!response.ok) return;
    const payload = await response.json();
    const accessToken = Array.isArray(payload) ? payload[0]?.access_token : payload?.access_token;
    if (accessToken) {
      localStorage.setItem('token', accessToken);
    }
  } catch {
    // No token store in front of this deployment — carry on unauthenticated.
  }
}

/**
 * Mint the short-lived token appended to generated-image URLs, and keep it
 * fresh. Images are loaded by a plain <img src> and cannot send a header.
 */
async function refreshImageToken(): Promise<void> {
  try {
    const { token, expires_in } = await apiService.getImageToken();
    setImageToken(token);
    // Re-mint a little before expiry so long-lived sessions keep rendering.
    const refreshInMs = Math.max((expires_in - 60), 60) * 1000;
    window.setTimeout(refreshImageToken, refreshInMs);
  } catch {
    setImageToken(null);
  }
}

const AppWrapper = () => {
  // State to store the current theme
  const [isConfigLoaded, setIsConfigLoaded] = useState(false);
  const [isUserInfoLoaded, setIsUserInfoLoaded] = useState(false);
  const [isDarkMode, setIsDarkMode] = useState(
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
  type ConfigType = typeof defaultConfig;
  const [config, setConfig] = useState<ConfigType>(defaultConfig);
  useEffect(() => {
    const initConfig = async () => {
      window.appConfig = config;
      setEnvData(config);
      setApiUrl(config.API_URL);
      try {
        const response = await fetch('/config');
        let config = defaultConfig;
        if (response.ok) {
          config = await response.json();
          config.ENABLE_AUTH = toBoolean(config.ENABLE_AUTH);
        }

        window.appConfig = config;
        setEnvData(config);
        setApiUrl(config.API_URL);
        setConfig(config);
        const defaultUserInfo = await getUserInfo();
        window.userInfo = defaultUserInfo;
        setUserInfoGlobal(defaultUserInfo);
        await apiService.sendUserBrowserLanguage();
        await acquireApiAccessToken();
        await refreshImageToken();
      } catch (error) {
          console.info("frontend config did not load from python", error);
      } finally {
        setIsConfigLoaded(true);
        setIsUserInfoLoaded(true);
      }
    };
    
    initConfig(); // Call the async function inside useEffect
    // Intentionally mount-only: this bootstraps `config` once at startup. Adding it
    // as a dependency would re-run initialisation every time the value it sets changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Effect to listen for changes in the user's preferred color scheme
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

    const handleThemeChange = (event: MediaQueryListEvent) => {
      setIsDarkMode(event.matches);
      document.body.classList.toggle("dark-mode", event.matches);
    };

    // Apply dark-mode class initially
    document.body.classList.toggle("dark-mode", isDarkMode);

    mediaQuery.addEventListener("change", handleThemeChange);
    return () => mediaQuery.removeEventListener("change", handleThemeChange);
    // Intentionally mount-only: `isDarkMode` is read once to apply the initial class.
    // Thereafter the media-query listener drives it, so adding the dependency would
    // only tear down and re-register that listener on every theme change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!isConfigLoaded || !isUserInfoLoaded) return <div>Loading...</div>;
  return (
    <StrictMode>
      <ReduxProvider store={store}>
        <FluentProvider theme={isDarkMode ? teamsDarkTheme : teamsLightTheme} style={{ height: "100vh" }}>
          <App />
        </FluentProvider>
      </ReduxProvider>
    </StrictMode>
  );
};
root.render(<AppWrapper />);
reportWebVitals();
