{
  description = "Standing Order Job — pinned offline Nix build for reproducible MeitouClient mod releases";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    meitou-client = {
      url = "github:VlcekM/MeitouClient/v0.24.0";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      meitou-client,
    }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          lib = pkgs.lib;

          meitouMeta = builtins.fromJSON (builtins.readFile ./meitou.json);
          modVersion = lib.strings.trim (builtins.readFile ./version.txt);
          packageName = "StandingOrderJob";
          meitouTag = lib.strings.trim (builtins.readFile ./.github/meitou-client.ref);
          meitouCommitShort = builtins.substring 0 9 (meitou-client.rev or "unknown");
          sourceRev = self.rev or "0000000000000000000000000000000000000000";

          nugetDeps = pkgs.mkNugetDeps {
            name = "standing-order-job-nuget-deps";
            sourceFile = ./nix/deps.json;
            installable = true;
          };

          nugetSource = pkgs.mkNugetSource {
            name = "standing-order-job-nuget-source";
            deps = [ nugetDeps ];
          };

          modSrc = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./src
              ./tools
              ./meitou.json
              ./version.txt
              ./.editorconfig
            ];
          };

          mkRelease =
            {
              includeBinlog ? false,
            }:
            pkgs.stdenvNoCC.mkDerivation {
              pname = "standing-order-job";
              version = modVersion;
              src = modSrc;

              nativeBuildInputs = [
                pkgs.dotnetCorePackages.sdk_11_0
                pkgs.python3
                pkgs.coreutils
              ];

              # Offline Nix sandbox build: no network, no host $HOME, deterministic timestamps & paths.
              buildPhase = ''
                runHook preBuild

                # Nix mkDerivation exports `version` (the mod's version, e.g. 0.1.2), which MSBuild
                # otherwise picks up as $(Version) when building MeitouClient ProjectReferences and
                # trips ApiCompat CP0003 against the Meitou.References baseline.
                unset version
                export HOME="$TMPDIR/home"
                export DOTNET_CLI_HOME="$TMPDIR/dotnet-cli"
                export DOTNET_CLI_TELEMETRY_OPTOUT=1
                export DOTNET_NOLOGO=1
                export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
                # Trailing slash required: upstream Directory.Build.targets concatenates
                # $(NuGetPackageRoot)meitou.references/... directly without a separator.
                export NUGET_PACKAGES="$TMPDIR/nuget-packages/"
                mkdir -p "$HOME" "$DOTNET_CLI_HOME" "$NUGET_PACKAGES" "$TMPDIR/work"

                cat > "$TMPDIR/NuGet.Config" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="nix" value="${nugetSource}/lib" />
  </packageSources>
</configuration>
EOF

                cp -R "$src" "$TMPDIR/work/mod"
                cp -R "${meitou-client}" "$TMPDIR/work/MeitouClient"
                chmod -R u+w "$TMPDIR/work"

                meitou_root="$TMPDIR/work/MeitouClient"
                mod_root="$TMPDIR/work/mod"
                out_pkg="$mod_root/out/${packageName}"
                mkdir -p "$out_pkg"

                dotnet restore "$mod_root/src/StandingOrderJob.csproj" \
                  --configfile "$TMPDIR/NuGet.Config" \
                  -p:MeitouClientRoot="$meitou_root"

                dotnet restore "$mod_root/tools/StandingOrderJobPackage.csproj" \
                  --configfile "$TMPDIR/NuGet.Config" \
                  -p:MeitouClientRoot="$meitou_root"

                dotnet build --no-restore -c Release -m:1 \
                  "$mod_root/src/StandingOrderJob.csproj" \
                  -p:MeitouClientRoot="$meitou_root" \
                  -p:CI=true \
                  -p:DeterministicBuild=true \
                  -p:ContinuousIntegrationBuild=true \
                  -p:EnableSourceControlManagerQueries=false \
                  -p:SourceRevisionId="${sourceRev}" \
                  -p:BuildGitCommit="${meitouCommitShort}" \
                  -p:BuildGitTag="${meitouTag}" \
                  -bl:"$mod_root/out/${packageName}.binlog"

                dotnet build --no-restore -c Release -m:1 \
                  "$mod_root/tools/StandingOrderJobPackage.csproj" \
                  -p:MeitouClientRoot="$meitou_root"

                dotnet run --no-build -c Release \
                  --project "$mod_root/tools/StandingOrderJobPackage.csproj" \
                  -p:MeitouClientRoot="$meitou_root" \
                  -- "$out_pkg/${packageName}.mod"

                cp "$mod_root/meitou.json" "$out_pkg/meitou.json"
                cp "$mod_root/src/bin/Release/net11.0/${packageName}.dll" "$out_pkg/${packageName}.dll"

                bash "$mod_root/tools/archive-package.sh" >/dev/null
                bash "$mod_root/tools/check-binlog-secrets.sh" "$mod_root/out/${packageName}.binlog"

                runHook postBuild
              '';

              installPhase = ''
                runHook preInstall

                mkdir -p "$out"
                cp -R "$TMPDIR/work/mod/out/${packageName}" "$out/${packageName}"
                cp "$TMPDIR/work/mod/out/${packageName}-${modVersion}.zip" "$out/"
                cp "$TMPDIR/work/mod/out/${packageName}-${modVersion}.contents.sha256" "$out/"
                cp "$TMPDIR/work/mod/out/${packageName}-${modVersion}.zip.sha256" "$out/"
                ${lib.optionalString includeBinlog ''
                  cp "$TMPDIR/work/mod/out/${packageName}.binlog" "$out/"
                ''}

                runHook postInstall
              '';

              meta = {
                description = "Standing Order Job Meitou mod (${meitouMeta.gameVersion})";
                license = lib.licenses.gpl3Only;
              };
            };

          release = mkRelease { includeBinlog = false; };
          release-with-binlog = mkRelease { includeBinlog = true; };
        in
        {
          default = release;
          inherit release release-with-binlog nugetSource;
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.dotnetCorePackages.sdk_11_0
              pkgs.ilspycmd
              pkgs.nuget-to-json
              pkgs.python3
            ];
          };
        }
      );
    };
}
