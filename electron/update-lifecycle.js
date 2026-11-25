'use strict';

const POST_INSTALL_LAUNCH_ARGS = new Set([
  '--dflash-post-update',
  '--dflash-post-install',
]);

function hasPostInstallLaunchArg(argv = process.argv) {
  return Array.isArray(argv) && argv.some((arg) => POST_INSTALL_LAUNCH_ARGS.has(arg));
}

// How long after an install or update the automatic check stays quiet. Before
// this limit the quiet period had no end: the post-update welcome flag is only
// cleared by closing or minimizing the window, so a user who quit from the
// tray never got another automatic update (found in the end-to-end test).
const POST_INSTALL_QUIET_MS = 10 * 60 * 1000;

function shouldSuppressAutomaticUpdateCheck(options = {}) {
  const uptimeMs = Number(options.uptimeMs);
  if (Number.isFinite(uptimeMs) && uptimeMs >= POST_INSTALL_QUIET_MS) return false;
  return Boolean(
    options.postInstallWelcomeActive
      || options.postInstallSetupActive
      || hasPostInstallLaunchArg(options.argv),
  );
}

module.exports = {
  POST_INSTALL_QUIET_MS,
  POST_INSTALL_LAUNCH_ARGS,
  hasPostInstallLaunchArg,
  shouldSuppressAutomaticUpdateCheck,
};
