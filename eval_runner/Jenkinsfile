pipeline {
    agent any

    parameters {
        string(name: 'TARGET_URL', defaultValue: '', description: '평가 대상 외부 AI API 주소')
        string(name: 'TARGET_TYPE', defaultValue: 'http', description: '어댑터 타입 (http/dify 등)')
        string(name: 'HOST_DATA_PATH', defaultValue: '/Users/luuuuunatic/Developer/dscore-ttc/data/knowledges/eval/data', description: '호스트의 golden.csv 경로')
    }

    environment {
        // M1 Max 호스트에서 실행 중인 Ollama 주소
        OLLAMA_HOST = "http://host.docker.internal:11434"
    }

    stages {
        stage('1. 환경 검증') {
            steps {
                script {
                    if (params.TARGET_URL == '') error "TARGET_URL 파라미터가 필요합니다."
                }
            }
        }

        stage('2. 평가 실행 (Fail-Fast)') {
            steps {
                // 보안을 위해 API Key는 Jenkins Credentials에서 안전하게 가져옵니다.
                withCredentials([string(credentialsId: 'external-ai-api-key', variable: 'SAFE_API_KEY')]) {
                    sh """
                    docker run --rm \
                        --network devops-net \
                        -v ${params.HOST_DATA_PATH}:/app/data \
                        -v /var/jenkins_home/scripts/eval_runner/adapters:/app/adapters \
                        -v /var/jenkins_home/scripts/eval_runner/tests:/app/tests \
                        -v /var/jenkins_home/scripts/eval_runner/configs:/app/configs \
                        -e OLLAMA_BASE_URL=${env.OLLAMA_HOST} \
                        -e TARGET_URL='${params.TARGET_URL}' \
                        -e TARGET_TYPE='${params.TARGET_TYPE}' \
                        -e API_KEY='${SAFE_API_KEY}' \
                        -e BUILD_TAG='${env.BUILD_TAG}' \
                        dscore-eval-runner:v1-fat \
                        pytest /app/tests/test_runner.py -n 1 --junitxml=/app/data/results.xml
                    """
                }
            }
        }
    }

    post {
        always {
            // Jenkins UI에 테스트 결과 리포트를 게시합니다.
            junit 'data/results.xml'
        }
    }
}