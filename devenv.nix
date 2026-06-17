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
      };

      services.keycloak = {
        enable = true;
        settings.http-port = 8081;
        realms.operaton = {
          path = "./fixture/keycloak/operaton-realm.json";
          import = true;
          # export = true;  # breaks devenv test when enabled, because it never completes
        };
      };

      processes.operaton.ready.exec = lib.mkForce ''
        bash -ec 'code="$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/engine-rest/engine)"; [ "$code" = "200" ] || [ "$code" = "401" ]'
      '';

      languages.python = {
        enable = true;
        package = pkgs.python312;
        venv.enable = true;
        uv = {
          enable = true;
          sync = {
            enable = true;
            allGroups = true;
            extras = [ "cli" ];
          };
        };
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
      ];

      dotenv.enable = true;

      enterShell = ''
        unset PYTHONPATH
      '';

      enterTest = ''
        wait_for_port 8080 60
        wait_for_port 8081 60
        make test
        curl -sf -X POST -H "Content-Type: application/json" -d '{}' http://localhost:8080/engine-rest/process-definition/key/operaton-tasks-hello-world/start
        operaton-tasks serve examples/hello_world.py --limit 1 --run-timeout 60
      '';

    };
in
{
  profiles.shell.module = {
    imports = [ shell ];
  };
  cachix.pull = [ "vasara-bpm" ];
}
