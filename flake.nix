{
  description = "Smartbox python package";

  inputs = {
    nixpkgs.url = "nixpkgs/nixos-unstable";
    nur.url = "github:nix-community/NUR";
  };

  outputs = { self, nixpkgs, nur }: let
    system = "x86_64-linux";
    pkgs = import nixpkgs {
      inherit system;
      overlays = [
        (self: super: {
          nur = import nur {
            nurpkgs = self;
            pkgs = self;
          };
        })
      ];
    };
  in {
    devShells.${system}.default = pkgs.mkShell {
      inputsFrom = [
        pkgs.nur.repos.graham33.hass-smartbox
      ];
      packages = with pkgs; [
        black
      ];
    };
  };
}