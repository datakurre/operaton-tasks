{
  description = "Operaton External Service Task Client";

  nixConfig = {
    extra-trusted-public-keys = "devenv.cachix.org-1:w1cLUi8dv3hnoSPGAuibQv+f9TZLr6cv/Hm9XgU50cw=";
    extra-substituters = "https://devenv.cachix.org";
  };

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    devenv.url = "github:cachix/devenv";
    mvn2nix.url = "github:datakurre/mvn2nix";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { self, ... }@inputs:
    inputs.flake-utils.lib.eachDefaultSystem (
      localSystem:
      let
        inherit (inputs.nixpkgs) lib;
        pkgs = import inputs.nixpkgs {
          inherit localSystem;
          overlays = [ inputs.mvn2nix.overlay ];
        };
        jdk = pkgs.jdk17;
        maven = (pkgs.maven.override { jdk_headless = jdk; });
        python = pkgs.python312;
        uv = (
          pkgs.buildFHSEnv {
            name = "uv";
            targetPkgs = pkgs: [ pkgs.uv ];
            runScript = "uv";
          }
        );
        workspace = inputs.uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
        overlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel"; # or sourcePreference = "sdist";
        };
        pyprojectOverrides =
          _final: _prev:
          {
          };
        pythonSet =
          (pkgs.callPackage inputs.pyproject-nix.build.packages {
            inherit python;
          }).overrideScope
            (
              lib.composeManyExtensions [
                inputs.pyproject-build-systems.overlays.default
                overlay
                pyprojectOverrides
              ]
            );
        name = "operaton-tasks";
      in
      {
        # Opinionated IDE
        apps.ide =
          let
            ide = pkgs.vscode-with-extensions.override {
              vscode = pkgs.vscodium;
              vscodeExtensions = [
                pkgs.vscode-extensions.bbenoist.nix
                pkgs.vscode-extensions.ms-pyright.pyright
                pkgs.vscode-extensions.ms-python.python
                pkgs.vscode-extensions.ms-vscode.makefile-tools
                pkgs.vscode-extensions.vscodevim.vim
                (pkgs.vscode-extensions.charliermarsh.ruff.overrideAttrs (old: {
                  postInstall = ''
                    rm -f $out/share/vscode/extensions/charliermarsh.ruff/bundled/libs/bin/ruff
                    ln -s ${pkgs.ruff}/bin/ruff $out/share/vscode/extensions/charliermarsh.ruff/bundled/libs/bin/ruff
                  '';
                }))
              ];
            };
          in
          {
            type = "app";
            program = "${ide}/bin/codium";
          };

        # https://devenv.sh/guides/using-with-flakes/#the-flakenix-file
        packages.devenv-up = self.devShells.${localSystem}.default.config.procfileScript;
        packages.devenv-test = self.devShells.${localSystem}.default.config.test;

        # Jar
        packages.fixture = pkgs.callPackage ./fixture { inherit jdk maven; };

        # docs: https://pyproject-nix.github.io/uv2nix/usage/hello-world.html
        packages.default = pythonSet.mkVirtualEnv "${name}-env" workspace.deps.default;

        devShells.default =
          let
            editableOverlay = workspace.mkEditablePyprojectOverlay {
              root = "$REPO_ROOT";
            };
            editablePythonSet = pythonSet.overrideScope editableOverlay;
            virtualenv = editablePythonSet.mkVirtualEnv "${name}-dev-env" workspace.deps.all;
          in
          inputs.devenv.lib.mkShell {
            inherit inputs pkgs;
            modules = [
              (
                { pkgs, config, ... }:
                {
                  packages = [
                    jdk
                    maven
                    pkgs.entr
                    uv
                    virtualenv
                  ];
                  enterShell = ''
                    # Undo dependency propagation by nixpkgs.
                    unset PYTHONPATH

                    # Don't create venv using uv
                    export UV_NO_SYNC=1

                    # Prevent uv from downloading managed Python's
                    export UV_PYTHON_DOWNLOADS=never

                    # Get repository root using git. This is expanded at runtime by the editable `.pth` machinery.
                    export REPO_ROOT=$(git rev-parse --show-toplevel)
                  '';
                  processes.run.exec = "java -jar ${self.packages.${localSystem}.fixture}";
                }
              )
            ];
          };
        devShells.mvn2nix = pkgs.mkShell {
          packages = [
            jdk
            maven
            (inputs.mvn2nix.defaultPackage.${localSystem}.override {
              inherit jdk maven;
            })
          ];
        };
        devShells.impure = pkgs.mkShell {
          packages = [
            python
            uv
          ];
          shellHook = ''
            unset PYTHONPATH
            export UV_PYTHON_DOWNLOADS=never
          '';
        };
        formatter = pkgs.nixfmt-rfc-style;
      }
    );
}
