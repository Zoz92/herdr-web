{
  description = "herdr-web — piloter les agents herdr-core depuis un téléphone";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      pyenv = pkgs.python3.withPackages (p: [ p.pywebpush p.cryptography p.pytest ]);
    in {
      packages.x86_64-linux.pyenv = pyenv;
      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [ pyenv pkgs.ffmpeg pkgs.nodejs ];
      };
    };
}
