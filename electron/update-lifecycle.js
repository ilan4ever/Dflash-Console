'use strict';

const POST_INSTALL_LAUNCH_ARGS = new Set([
  '--dflash-post-update',
  '--dflash-post-install',
]);

function hasPostInstallLaunchArg(argv = process.argv) {
  return Array.isArray(argv) && argv.some((arg) => POST_INSTALL_LAUNCH_ARGS.has(arg));
}

function shouldSuppressAutomaticUpdateCheck(options = {}) {
  return Boolean(
    options.postInstallWelcomeActive
      || options.postInstallSetupActive
      || hasPostInstallLaunchArg(options.argv),
  );
}

module.exports = {
  POST_INSTALL_LAUNCH_ARGS,
  hasPostInstallLaunchArg,
  shouldSuppressAutomaticUpdateCheck,
};
