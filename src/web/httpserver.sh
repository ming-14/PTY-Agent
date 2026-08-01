if ! command -v http-server &> /dev/null; then
    echo "Error: http-server is not installed or not in PATH"
    exit 1
fi

cd "$(dirname "$0")/static" && http-server
