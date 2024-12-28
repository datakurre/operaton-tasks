{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
{
  imports = [
    ./devenv/modules/operaton.nix
    ./devenv/modules/python.nix
  ];

  package.operaton.path = ./fixture;

  languages.python.interpreter = pkgs.python312;
  languages.python.pyprojectOverrides = final: prev: {
    "operaton-tasks" = prev."operaton-tasks".overrideAttrs (old: {
      nativeBuildInputs =
        old.nativeBuildInputs
        ++ final.resolveBuildSystem ({
          "hatchling" = [ ];
        });
    });
  };

  packages = [
    pkgs.entr
    pkgs.findutils
    pkgs.gnumake
    pkgs.openssl
  ];

  dotenv.disableHint = true;

  enterShell = ''
    unset PYTHONPATH
    export UV_NO_SYNC=1
    export UV_PYTHON_DOWNLOADS=never
    export REPO_ROOT=$(git rev-parse --show-toplevel)
  '';

  processes.example.exec = "make -s watch";

  enterTest = ''
    wait_for_port 8080 60
  '';

  cachix.pull = [ "datakurre" ];

  devcontainer.enable = true;

  git-hooks.hooks.treefmt = {
    enable = true;
    settings.formatters = [
      pkgs.nixfmt-rfc-style
    ];
  };
}
