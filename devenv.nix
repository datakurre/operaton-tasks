{ ... }:
let
  shell =
    {
      lib,
      pkgs,
      devenv-module-operaton,
      ...
    }:
    {
      services.operaton = {
        enable = true;
        port = 8080;
        forwardHeadersStrategy = "native";
        package = devenv-module-operaton.packages.${pkgs.stdenv.hostPlatform.system}.default;
        deployment = ./fixture/operaton;
        oauth2 = {
          enable = true;
          issuerUri = "http://localhost:8081/realms/operaton";
        };
      };

      services.keycloak = {
        enable = true;
        settings.http-port = 8081;
        realms.operaton = {
          path = "./fixture/keycloak/operaton-realm.json";
          import = true;
          export = true;
        };
      };

      processes.operaton.ready.exec = lib.mkForce ''
        bash -ec 'code="$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/engine-rest/engine)"; [ "$code" = "200" ] || [ "$code" = "401" ]'
      '';

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

      treefmt = {
        enable = true;
        config.programs.nixfmt.enable = true;
      };

      git-hooks.hooks.treefmt.enable = true;

      packages = [
        pkgs.entr
        pkgs.findutils
        pkgs.gnumake
        pkgs.openssl
        (pkgs.python312.withPackages (
          ps: with ps; [
            aiohttp
            black
            fastapi
            flake8
            isort
            mypy
            pydantic
            pydantic-settings
            pytest
            pytest-cov
            starlette
            typer
            uvicorn
          ]
        ))
      ];

      dotenv.enable = true;

      enterShell = ''
        unset PYTHONPATH
      '';

      enterTest = ''
        wait_for_port 8080 60
      '';

    };
in
{
  profiles.shell.module = {
    imports = [ shell ];
  };
}
