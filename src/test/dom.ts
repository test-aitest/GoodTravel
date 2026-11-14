import { GlobalRegistrator } from "@happy-dom/global-registrator";

if (!GlobalRegistrator.isRegistered) GlobalRegistrator.register({ url: "http://localhost/" });

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
