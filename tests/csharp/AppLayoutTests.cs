using System;
using System.Collections.Generic;
using System.IO;
using DFlashConsoleLayout;

// Compiled together with tools/dflash-setup-ui/AppLayout.cs by
// tests/side-by-side.test.js. Exit code 0 = every check passed.
internal static class AppLayoutTests
{
    private static int _failures;

    private static int Main()
    {
        Run("versions compare by number", VersionsCompareByNumber);
        Run("pointers round-trip and reject junk", PointersRoundTrip);
        Run("a version that started is launched without counting", StartedVersionIsNotCounted);
        Run("a second launch while running is not counted", RunningVersionIsNotCounted);
        Run("two failed starts go back to the previous version", FailedStartsRollBack);
        Run("a missing pointer picks the newest usable version", MissingPointerPicksNewest);
        Run("no app folder means nothing to start", NothingToStart);
        Run("the old flat install is removed, other files stay", FlatInstallRemoved);
        Run("a folder without app.asar is never cleaned", NonFlatFolderUntouched);
        Run("old versions go, kept versions stay", OldVersionsRemoved);
        Console.WriteLine(_failures == 0 ? "ALL PASSED" : _failures + " FAILED");
        return _failures == 0 ? 0 : 1;
    }

    private static void Run(string name, Action<string> test)
    {
        string root = Path.Combine(Path.GetTempPath(), "dflash-layout-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            test(root);
            Console.WriteLine("ok   " + name);
        }
        catch (Exception ex)
        {
            _failures++;
            Console.WriteLine("FAIL " + name + ": " + ex.Message);
        }
        finally
        {
            try { Directory.Delete(root, true); } catch { }
        }
    }

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    private static void Equal(string expected, string actual, string what)
    {
        if (!string.Equals(expected, actual)) throw new Exception(what + ": expected '" + expected + "', got '" + actual + "'");
    }

    private static void MakeApp(string root, string version)
    {
        string dir = AppLayout.AppDir(root, version);
        Directory.CreateDirectory(dir);
        File.WriteAllText(Path.Combine(dir, AppLayout.ExeName), "MZ");
    }

    private static void Touch(string path, string text)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(path, text);
    }

    private static void VersionsCompareByNumber(string root)
    {
        Check(AppLayout.CompareVersions("0.3.10", "0.3.9") > 0, "0.3.10 > 0.3.9");
        Check(AppLayout.CompareVersions("1.0.0", "1.0.0-beta.1") > 0, "release after its pre-release");
        Check(AppLayout.CompareVersions("0.3.300", "0.3.300") == 0, "equal");
        Check(AppLayout.IsValidVersion("0.3.301"), "valid");
        Check(!AppLayout.IsValidVersion("..\\x"), "path is not a version");
    }

    private static void PointersRoundTrip(string root)
    {
        AppLayout.WritePointer(root, AppLayout.CurrentFile, "0.3.301");
        Equal("0.3.301", AppLayout.ReadPointer(root, AppLayout.CurrentFile), "current");
        AppLayout.WritePointer(root, AppLayout.CurrentFile, "0.3.302");
        Equal("0.3.302", AppLayout.ReadPointer(root, AppLayout.CurrentFile), "replaced");
        File.WriteAllText(Path.Combine(root, AppLayout.PreviousFile), "..\\..\\evil");
        Equal("", AppLayout.ReadPointer(root, AppLayout.PreviousFile), "junk pointer");
        Check(Directory.GetFiles(root, "*.tmp-*").Length == 0, "no temporary files left");
    }

    private static void StartedVersionIsNotCounted(string root)
    {
        MakeApp(root, "0.3.301");
        AppLayout.WritePointer(root, AppLayout.CurrentFile, "0.3.301");
        Touch(Path.Combine(AppLayout.AppDir(root, "0.3.301"), AppLayout.StartedOkFile), "x");
        AppLayout.LaunchChoice choice = AppLayout.ChooseLaunchVersion(root, v => false);
        Equal("0.3.301", choice.Version, "version");
        Check(!choice.CountedAttempt, "not counted");
        Equal("0", AppLayout.ReadAttempts(root, "0.3.301").ToString(), "attempts");
    }

    private static void RunningVersionIsNotCounted(string root)
    {
        MakeApp(root, "0.3.301");
        AppLayout.WritePointer(root, AppLayout.CurrentFile, "0.3.301");
        AppLayout.LaunchChoice choice = AppLayout.ChooseLaunchVersion(root, v => v == "0.3.301");
        Equal("0.3.301", choice.Version, "version");
        Check(!choice.CountedAttempt, "not counted while running");
    }

    private static void FailedStartsRollBack(string root)
    {
        MakeApp(root, "0.3.300");
        MakeApp(root, "0.3.301");
        Touch(Path.Combine(AppLayout.AppDir(root, "0.3.300"), AppLayout.StartedOkFile), "x");
        AppLayout.WritePointer(root, AppLayout.PreviousFile, "0.3.300");
        AppLayout.WritePointer(root, AppLayout.CurrentFile, "0.3.301");

        AppLayout.LaunchChoice first = AppLayout.ChooseLaunchVersion(root, v => false);
        Equal("0.3.301", first.Version, "first start");
        Check(first.CountedAttempt, "first counted");
        AppLayout.LaunchChoice second = AppLayout.ChooseLaunchVersion(root, v => false);
        Equal("0.3.301", second.Version, "second start");
        AppLayout.LaunchChoice third = AppLayout.ChooseLaunchVersion(root, v => false);
        Equal("0.3.300", third.Version, "back to previous");
        Equal("0.3.301", third.RolledBackFrom, "rolled back from");
        Equal("0.3.300", AppLayout.ReadPointer(root, AppLayout.CurrentFile), "pointer moved back");
        Check(File.Exists(Path.Combine(AppLayout.AppDir(root, "0.3.301"), AppLayout.BadFile)), "failed version marked bad");
        Equal("0.3.301", File.ReadAllText(Path.Combine(root, AppLayout.RolledBackFile)).Trim(), "rolled-back.txt");
        AppLayout.LaunchChoice fourth = AppLayout.ChooseLaunchVersion(root, v => false);
        Equal("0.3.300", fourth.Version, "stays on previous");
    }

    private static void MissingPointerPicksNewest(string root)
    {
        MakeApp(root, "0.3.9");
        MakeApp(root, "0.3.10");
        MakeApp(root, "0.3.11");
        Touch(Path.Combine(AppLayout.AppDir(root, "0.3.11"), AppLayout.BadFile), "x");
        Directory.CreateDirectory(Path.Combine(root, "app-0.3.12" + AppLayout.PartialSuffix));
        AppLayout.LaunchChoice choice = AppLayout.ChooseLaunchVersion(root, v => false);
        Equal("0.3.10", choice.Version, "newest usable");
        Equal("0.3.10", AppLayout.ReadPointer(root, AppLayout.CurrentFile), "pointer repaired");
    }

    private static void NothingToStart(string root)
    {
        AppLayout.LaunchChoice choice = AppLayout.ChooseLaunchVersion(root, v => false);
        Equal("", choice.Version, "no version");
    }

    private static void FlatInstallRemoved(string root)
    {
        Touch(Path.Combine(root, "resources", "app.asar"), "old");
        Touch(Path.Combine(root, "locales", "en-US.pak"), "old");
        Touch(Path.Combine(root, "ffmpeg.dll"), "old");
        Touch(Path.Combine(root, AppLayout.ExeName), "launcher");
        Touch(Path.Combine(root, "dflash-setup-ui.exe"), "uninstaller");
        Touch(Path.Combine(root, "install-version.txt"), "0.3.301");
        MakeApp(root, "0.3.301");
        string app = AppLayout.AppDir(root, "0.3.301");
        Touch(Path.Combine(app, "resources", "app.asar"), "new");
        Touch(Path.Combine(app, "locales", "en-US.pak"), "new");
        Touch(Path.Combine(app, "ffmpeg.dll"), "new");

        List<string> failed = AppLayout.RemoveFlatInstall(root, app);
        Check(failed.Count == 0, "nothing failed");
        Check(!Directory.Exists(Path.Combine(root, "resources")), "old resources removed");
        Check(!Directory.Exists(Path.Combine(root, "locales")), "old locales removed");
        Check(!File.Exists(Path.Combine(root, "ffmpeg.dll")), "old dll removed");
        Check(File.Exists(Path.Combine(root, AppLayout.ExeName)), "launcher kept");
        Check(File.Exists(Path.Combine(root, "dflash-setup-ui.exe")), "uninstaller kept");
        Check(File.Exists(Path.Combine(root, "install-version.txt")), "version file kept");
        Check(File.Exists(Path.Combine(app, "resources", "app.asar")), "new app untouched");
    }

    private static void NonFlatFolderUntouched(string root)
    {
        Touch(Path.Combine(root, "resources", "readme.txt"), "someone else's");
        MakeApp(root, "0.3.301");
        Touch(Path.Combine(AppLayout.AppDir(root, "0.3.301"), "resources", "app.asar"), "new");
        AppLayout.RemoveFlatInstall(root, AppLayout.AppDir(root, "0.3.301"));
        Check(File.Exists(Path.Combine(root, "resources", "readme.txt")), "kept");
    }

    private static void OldVersionsRemoved(string root)
    {
        MakeApp(root, "0.3.299");
        MakeApp(root, "0.3.300");
        MakeApp(root, "0.3.301");
        Directory.CreateDirectory(Path.Combine(root, "app-0.3.302" + AppLayout.PartialSuffix));
        AppLayout.RemoveOldVersions(root, "0.3.301", "0.3.300");
        Check(!Directory.Exists(AppLayout.AppDir(root, "0.3.299")), "0.3.299 removed");
        Check(Directory.Exists(AppLayout.AppDir(root, "0.3.300")), "previous kept");
        Check(Directory.Exists(AppLayout.AppDir(root, "0.3.301")), "current kept");
        Check(!Directory.Exists(Path.Combine(root, "app-0.3.302" + AppLayout.PartialSuffix)), "partial removed");
    }
}
