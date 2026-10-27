. "$1/linux/lib.sh"
pid_is $$ bash && echo self-ok
pid_is $$ nonesuch-program || echo other-no
pid_is "1; rm -rf /" bash || echo bad-no
pid_is "" bash || echo empty-no
